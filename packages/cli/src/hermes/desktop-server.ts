/**
 * Hermes Desktop 호환 서버. Hermes 데스크톱 앱이 원격 게이트웨이로 접속하면
 * `/api/health`·`/api/status`(HTTP)와 `/api/ws`(JSON-RPC)만으로 Perdix와 대화하게 한다.
 * 대화·도구·권한은 HermesAdapter가 ACP로 Perdix 서버에 넘기고, 여기서는 전송만 맡는다.
 * Hermes 앱은 수정하지 않는다(ADR 0036).
 */
import { randomBytes, timingSafeEqual } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { Duplex } from "node:stream";
import { WebSocketServer } from "ws";
import type { HermesAdapter } from "./adapter.js";
import { HermesSocketHub } from "./bridge.js";
import { PERDIX_HERO, PERDIX_LOGO } from "./branding.js";
import { VERSION } from "../version.js";
import { readFlowCatalog, readLangflowInfo, type FlowEntry } from "./flows.js";
import { listRelayModels, RelayRequestError } from "./relay-client.js";
import { DesktopConfigStore } from "./desktop-config.js";
import {
  LangflowApiError, type FlowData, type LangflowConfig, createFlow, deleteFlow, getFlow, listComponents, listFlows,
  readLangflowConfig, updateFlowData,
} from "./langflow-client.js";
import { LANGFLOW_PLUGIN_ID, LANGFLOW_PLUGIN_SOURCE } from "./langflow-plugin.js";
import { WORKFLOW_PLUGIN_ID, WORKFLOW_PLUGIN_SOURCE } from "./workflow-plugin.js";
import { SETTINGS_PLUGIN_ID, SETTINGS_PLUGIN_SOURCE } from "./settings-plugin.js";

export interface HermesDesktopServer {
  readonly url: string;
  readonly token: string;
  close(): Promise<void>;
}

export interface HermesDesktopServerOptions {
  readonly port?: number;
  readonly cwd?: string;
  /** `--langflow-config` 경로. 있으면 플로우를 Skills 화면에 보여준다. */
  readonly langflowConfig?: string;
  /** 지정하면 `<경로>/desktop-plugins/langflow/plugin.js`에 Langflow 플러그인을 설치한다(HERMES_HOME). */
  readonly hermesHome?: string;
  readonly token?: string;
  /** perdix-relay 토큰 파일. 기본 `~/.perdix/relay-token`(테스트에서 바꾼다). */
  readonly relayTokenFile?: string;
  readonly log?: (line: string) => void;
}

const MAX_PAYLOAD = 32 * 1024 * 1024;
const PUBLIC_PATHS = new Set(["/api/health", "/api/status"]);

type Json = Record<string, unknown>;

function tokensEqual(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left, right);
}

/** 헤더(Authorization Bearer, X-Hermes-*)나 쿼리에서 자격 증명 후보를 모은다. */
function credentials(req: IncomingMessage, url: URL): string[] {
  const found: string[] = [];
  const auth = req.headers.authorization;
  if (auth?.toLowerCase().startsWith("bearer ")) found.push(auth.slice(7).trim());
  for (const [name, value] of Object.entries(req.headers)) {
    if (name.startsWith("x-hermes") && typeof value === "string") found.push(value);
  }
  for (const key of ["token", "ticket", "session_token"]) {
    const value = url.searchParams.get(key);
    if (value) found.push(value);
  }
  return found;
}

function health(): Json {
  return { ok: true, version: VERSION, displayVersion: `Perdix ${VERSION}`, commit: "perdix", auth_required: true };
}

function status(): Json {
  return {
    version: VERSION, release_date: "", config_version: 0, latest_config_version: 0,
    can_update_hermes: false, gateway_running: true, gateway_state: "running",
    gateway_platforms: {}, gateway_exit_reason: null, gateway_updated_at: null,
    gateway_heartbeat_stale_s: null, gateway_shared_with: null, active_agents: 0,
    gateway_busy: false, gateway_drainable: true, restart_drain_timeout: 0, active_sessions: 0,
    auth_required: true, components: {}, overall: "ok", profiles: [], parked_profiles: [],
    gateway_mode: "standalone", multiplex_standalone_reason: null, shared_profile_warning: false,
  };
}

interface RouteContext {
  readonly flows: () => FlowEntry[];
  readonly langflowUi: () => Promise<Json>;
  readonly langflowConfig: () => LangflowConfig | undefined;
  readonly relayTokenFile: string | undefined;
  readonly desktopConfig: DesktopConfigStore;
  readonly adapter: HermesAdapter;
  readonly url: URL;
  readonly cwd: string;
  readonly body: unknown;
}

type Route = (ctx: RouteContext) => Json | unknown[] | Promise<Json | unknown[]>;

/** 본문이 있는 요청(PATCH/POST)만 JSON으로 읽는다. 비어 있거나 GET이면 undefined. */
async function readJsonBody(req: IncomingMessage): Promise<unknown> {
  if (req.method === "GET" || req.method === "HEAD") return undefined;
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req as AsyncIterable<Buffer>) {
    size += chunk.length;
    if (size > MAX_PAYLOAD) throw new Error("요청 본문이 너무 큽니다");
    chunks.push(chunk);
  }
  if (size === 0) return undefined;
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

/** Langflow가 안 켜져 있거나 호출이 실패하면 `{error}`로 감싸고 던지지 않는다 — 500으로 뭉개지 않기 위해. */
async function withLangflow<T>(ctx: RouteContext, run: (config: LangflowConfig) => Promise<T>): Promise<Json> {
  const config = ctx.langflowConfig();
  if (config === undefined) return { ok: false, error: "disabled" };
  try {
    return { ok: true, result: await run(config) };
  } catch (error) {
    const detail = error instanceof LangflowApiError ? error.message
      : error instanceof Error ? error.message : String(error);
    return { ok: false, error: "request_failed", detail };
  }
}

/** 상태 코드와 화면에 보일 문구를 정해서 실패를 알리는 경로 오류. 그 밖의 예외는 500으로 나간다. */
export class RouteError extends Error {
  constructor(readonly status: number, message: string) { super(message); }
}

/** 모델 목록을 읽으려면 세션이 하나 필요하다. 처음 필요할 때 만들고, 사라졌으면(서버 재시작) 다시 만든다. */
const bootstrap = new WeakMap<HermesAdapter, Promise<string>>();
async function ensureSession(adapter: HermesAdapter): Promise<string> {
  let pending = bootstrap.get(adapter);
  if (pending !== undefined && !adapter.hasSession(await pending.catch(() => ""))) {
    bootstrap.delete(adapter);
    pending = undefined;
  }
  if (pending === undefined) {
    pending = adapter.request("session.create", { cols: 80 }).then((result) => String((result as Json).session_id));
    bootstrap.set(adapter, pending);
    pending.catch(() => bootstrap.delete(adapter));
  }
  return pending;
}

async function modelOptions(ctx: RouteContext): Promise<Json> {
  const session_id = await ensureSession(ctx.adapter);
  return await ctx.adapter.request("model.options", { session_id }) as Json;
}

async function sessionPage(ctx: RouteContext): Promise<Json> {
  const limit = Number(ctx.url.searchParams.get("limit")) || 50;
  const offset = Number(ctx.url.searchParams.get("offset")) || 0;
  const { sessions } = await ctx.adapter.request("session.list", {}) as { sessions: Array<Json> };
  const rows = sessions.map((item) => ({
    id: item.id, source: "perdix", model: null, title: item.title || null, preview: item.preview ?? "",
    started_at: item.started_at ?? 0, last_active: item.started_at ?? 0, ended_at: null,
    message_count: 0, tool_call_count: 0, input_tokens: 0, output_tokens: 0, cwd: ctx.cwd,
    is_active: false,
  }));
  return { limit, offset, sessions: rows.slice(offset, offset + limit), total: rows.length,
    profile_totals: { default: rows.length }, errors: [] };
}

const profile = (cwd: string, model: string): Json => ({
  name: "default", is_default: true, has_env: false, model, provider: "perdix", path: cwd, skill_count: 0,
});

interface RoleInfo { readonly id: string; readonly description: string; readonly readOnly: boolean }
// server/src/perdix/harness/modes.py의 MODES와 server/src/perdix/subagents/presets.py의 PRESETS를
// 손으로 옮겨 적은 것이다 — 실시간 동기화가 아니다. 두 파일이 바뀌면 이 목록도 같이 바꿔야 한다.
// TODO: _perdix 확장 ACP 메서드로 실시간 조회하게 바꾸기(지금은 없음, docs/protocol.md §1.2 참고).
const ROLES: readonly RoleInfo[] = [
  { id: "general", description: "질문에 답하고 코드베이스를 탐색한다.", readOnly: true },
  { id: "plan", description: "파일을 고치지 않고 조사해서 계획만 제안한다.", readOnly: true },
  { id: "build", description: "읽기·수정·명령 실행으로 구현하고 검증한다.", readOnly: false },
  { id: "explore", description: "읽기 전용 탐색/요약. 양이 많고 판단은 얕다.", readOnly: true },
  { id: "executor", description: "부모가 쪼개준 과업 실행. 쓰기 가능, main보다 한 단계 낮은 effort.", readOnly: false },
  { id: "reviewer", description: "완료 조건 판정. 읽기 전용, 검증 게이트용.", readOnly: true },
  { id: "compactor", description: "히스토리 요약 전용, 빈번 호출.", readOnly: true },
  { id: "titler", description: "세션 제목 등 단발 저비용 잡무.", readOnly: true },
  { id: "debugger", description: "실패 원인 분석. 재현에 필요하면 쓰기·실행 도구도 쓸 수 있다.", readOnly: false },
  { id: "security-reviewer", description: "취약점·신뢰 경계 점검. 읽기 전용, 코드 리뷰 관점의 보안 검토.", readOnly: true },
];

const ROUTES: Record<string, Route> = {
  "GET /api/health": health,
  "GET /api/status": status,
  "GET /api/profiles": async (ctx) => {
    const options = await modelOptions(ctx).catch(() => ({ model: "" }) as Json);
    return { profiles: [profile(ctx.cwd, String(options.model ?? ""))] };
  },
  "GET /api/profiles/active": () => ({ active: "default", current: "default" }),
  "GET /api/profiles/sessions": sessionPage,
  "GET /api/profiles/sessions/sidebar": sessionPage,
  "GET /api/sessions": sessionPage,
  "GET /api/model/info": async (ctx) => {
    const options = await modelOptions(ctx);
    return { model: String(options.model ?? ""), provider: String(options.provider ?? "perdix") };
  },
  "GET /api/model/options": modelOptions,
  // Hermes 화면 설정 기록(언어 등). Perdix 에이전트 설정이 아니다(desktop-config.ts).
  "GET /api/config": (ctx) => ctx.desktopConfig.read(),
  "PUT /api/config": (ctx) => {
    const config = (ctx.body as Json | undefined)?.config;
    if (typeof config !== "object" || config === null || Array.isArray(config)) {
      throw new RouteError(400, "설정 형식이 올바르지 않습니다.");
    }
    ctx.desktopConfig.save(config as Json);
    return { ok: true };
  },
  // Hermes 설정 파일 기반 항목은 Perdix에 없다. 빈 목록을 줘서 Model 화면(Main model)만 열리게 하고,
  // 나머지 설정 메뉴는 perdix-settings 플러그인이 감춘다(settings-plugin.ts).
  "GET /api/config/schema": () => ({ fields: {}, category_order: [] }),
  // Main model 화면이 함께 읽는 보조 모델·Mixture of Agents 설정. Perdix에 없는 개념이라 비어 있다고 답한다
  // (해당 메뉴는 감춘다). 없으면 화면에 "Not Found"가 뜬다.
  "GET /api/model/auxiliary": async (ctx) => {
    const options = await modelOptions(ctx);
    return { main: { model: String(options.model ?? ""), provider: String(options.provider ?? "perdix") }, tasks: [] };
  },
  "GET /api/model/moa": () => ({ default_preset: "", active_preset: "", presets: {} }),
  // Main model = 기본 모드(general) 역할의 모델 할당(agents.json)과 이 연결의 새 대화 기본 모델.
  // 지금 보고 있는 대화도 바로 바꾼다. 할당은 ACP 세션 설정(agent_model_general)으로 Perdix가 저장한다.
  // general 할당은 Langflow 중계(perdix-relay)의 general 역할도 같이 쓴다.
  "POST /api/model/set": async (ctx) => {
    const body = (ctx.body ?? {}) as Json;
    const model = typeof body.model === "string" ? body.model.trim() : "";
    const provider = typeof body.provider === "string" ? body.provider : "";
    if (body.scope !== "main") throw new RouteError(400, "Perdix에서는 Main model만 바꿀 수 있습니다.");
    if (!model) throw new RouteError(400, "모델을 고르세요.");
    if (provider !== "openrouter") {
      throw new RouteError(400, "기본 모델로 저장할 수 있는 것은 OpenRouter 모델입니다. 다른 공급자 모델은 대화 화면의 모델 선택에서 그 대화에만 적용하세요.");
    }
    const session_id = await ensureSession(ctx.adapter);
    await ctx.adapter.request("slash.exec", { session_id, command: `/assign general ${model}` });
    // Hermes 새 대화는 실행 옵션 --model을 넘겨 agents.json보다 우선한다. 그 기본값도 같이 바꿔야
    // 저장한 모델로 새 대화가 시작된다(서버를 다시 띄우면 --model 값으로 돌아간다).
    ctx.adapter.setDefaultModel(model);
    await ctx.adapter.request("config.set", { session_id, key: "model", value: model });
    const current = ctx.adapter.currentSessionId();
    if (current !== undefined && current !== session_id) {
      // 작업 중인 대화는 바꾸지 않는다(어댑터가 거절한다). 기본값 저장은 이미 끝났다.
      await ctx.adapter.request("config.set", { session_id: current, key: "model", value: model }).catch(() => undefined);
    }
    return { ok: true, model, provider };
  },
  "GET /api/config/defaults": () => ({}),
  "GET /api/fs/default-cwd": (ctx) => ({ cwd: ctx.cwd, branch: "" }),
  "GET /api/tools/toolsets": () => [],
  "GET /api/tools/terminal/backends": () => ({ active: "local", backends: [] }),
  // Langflow 플로우를 Hermes의 스킬 목록에 보여준다. 실행은 `/flow <별칭> <입력>`이다.
  // 플러그인(Langflow 화면 탭)이 주소를 묻는 곳. 실제로 떠 있는지도 알려 준다.
  "GET /api/plugins/langflow/config": (ctx) => ctx.langflowUi(),
  "GET /api/plugins/workflow/roles": () => [...ROLES],
  "GET /api/plugins/workflow/langflow-url": (ctx) => ctx.langflowUi(),
  // 노드 에디터용: Langflow 소스는 안 건드리고 공개 REST만 중계한다(ADR 0026 규칙 2/3와 같은 원칙,
  // 플러그인이 직접 Langflow를 fetch하면 CORS·API 키 노출 문제가 생겨서 여기서 대신 호출한다).
  "GET /api/plugins/workflow/langflow/flows": (ctx) => withLangflow(ctx, (config) => listFlows(config)),
  "GET /api/plugins/workflow/langflow/flow": (ctx) => {
    const id = ctx.url.searchParams.get("id");
    if (!id) return Promise.resolve({ ok: false, error: "missing_id" });
    return withLangflow(ctx, (config) => getFlow(config, id));
  },
  "POST /api/plugins/workflow/langflow/flow": (ctx) => {
    const name = (ctx.body as Json | undefined)?.name;
    if (typeof name !== "string" || name.trim() === "") {
      return Promise.resolve({ ok: false, error: "invalid_request" });
    }
    return withLangflow(ctx, (config) => createFlow(config, name.trim()));
  },
  "PATCH /api/plugins/workflow/langflow/flow": (ctx) => {
    const id = ctx.url.searchParams.get("id");
    const data = (ctx.body as Json | undefined)?.data;
    if (!id || typeof data !== "object" || data === null) {
      return Promise.resolve({ ok: false, error: "invalid_request" });
    }
    return withLangflow(ctx, async (config) => {
      await updateFlowData(config, id, data as unknown as FlowData);
      return { saved: true };
    });
  },
  // 플로우 삭제. 채팅 별칭(langflow.json의 flows)이 이 플로우를 가리키면 그 별칭은 끊긴다.
  // 설정 파일은 사용자 파일이라 여기서 고치지 않고, 끊긴 별칭 이름을 돌려줘 화면에서 알린다.
  "DELETE /api/plugins/workflow/langflow/flow": (ctx) => {
    const id = ctx.url.searchParams.get("id");
    if (!id) return Promise.resolve({ ok: false, error: "missing_id" });
    return withLangflow(ctx, async (config) => {
      await deleteFlow(config, id);
      return { id, aliases: ctx.flows().filter((flow) => flow.id === id).map((flow) => flow.alias) };
    });
  },
  // Perdix Agent 노드의 Model 칸 목록. 노드에 적힌 중계 주소(loopback만)의 모델 카탈로그를 읽는다.
  "GET /api/plugins/workflow/relay/models": async (ctx) => {
    const url = ctx.url.searchParams.get("url");
    if (!url) return { ok: false, error: "missing_url" };
    try {
      return { ok: true, result: await listRelayModels(url, ctx.relayTokenFile) };
    } catch (error) {
      if (error instanceof RelayRequestError) return { ok: false, error: "relay_failed", detail: error.message };
      throw error;
    }
  },
  "GET /api/plugins/workflow/langflow/components": (ctx) => withLangflow(ctx, (config) => listComponents(config)),
  "GET /api/skills": (ctx) => ctx.flows().map((flow) => ({
    name: flow.alias, category: "Langflow", enabled: true, provenance: "external",
    description: `${flow.description || "Langflow 플로우"} — /flow ${flow.alias} <입력>`,
  })),
  "GET /api/cron/jobs": () => [],
  "GET /api/cron/delivery-targets": () => ({ targets: [] }),
  "GET /api/messaging/platforms": () => ({ platforms: [] }),
  "GET /api/pairing": () => ({ approved: [], pending: [] }),
  "GET /api/audio/voice-live/status": () => ({ ok: false, available: false, reason: "Perdix에서는 음성을 지원하지 않습니다" }),
  "GET /api/hermes/update/check": () => ({
    install_method: "perdix", current_version: VERSION, behind: null, update_available: false, can_apply: false,
  }),
};

function sendJson(res: ServerResponse, code: number, body: unknown): void {
  const text = JSON.stringify(body);
  res.writeHead(code, { "Content-Type": "application/json; charset=utf-8", "Content-Length": Buffer.byteLength(text) });
  res.end(text);
}

export async function startHermesDesktopServer(
  adapter: HermesAdapter, options: HermesDesktopServerOptions = {},
): Promise<HermesDesktopServer> {
  const token = options.token ?? randomBytes(32).toString("hex");
  const log = options.log ?? (() => {});
  const cwd = options.cwd ?? process.cwd();
  const flows = (): FlowEntry[] => readFlowCatalog(options.langflowConfig, log);
  const desktopConfig = new DesktopConfigStore(options.hermesHome);
  const langflowConfig = (): LangflowConfig | undefined => readLangflowConfig(options.langflowConfig, log);
  const langflowUi = async (): Promise<Json> => {
    const info = readLangflowInfo(options.langflowConfig, log);
    if (info === undefined) return { url: null, ready: false };
    const ready = await fetch(`${info.baseUrl}/health`, { signal: AbortSignal.timeout(1500) })
      .then((response) => response.ok, () => false);
    return { url: info.baseUrl, ready };
  };
  if (options.hermesHome !== undefined && readLangflowInfo(options.langflowConfig, log) !== undefined) {
    const directory = join(options.hermesHome, "desktop-plugins", LANGFLOW_PLUGIN_ID);
    mkdirSync(directory, { recursive: true });
    writeFileSync(join(directory, "plugin.js"), LANGFLOW_PLUGIN_SOURCE);
    log(`Langflow 플러그인을 설치했습니다: ${directory}`);
  }
  if (options.hermesHome !== undefined) {
    const directory = join(options.hermesHome, "desktop-plugins", WORKFLOW_PLUGIN_ID);
    mkdirSync(directory, { recursive: true });
    writeFileSync(join(directory, "plugin.js"), WORKFLOW_PLUGIN_SOURCE);
    log(`Workflow 플러그인을 설치했습니다: ${directory}`);
    const settings = join(options.hermesHome, "desktop-plugins", SETTINGS_PLUGIN_ID);
    mkdirSync(settings, { recursive: true });
    writeFileSync(join(settings, "plugin.js"), SETTINGS_PLUGIN_SOURCE);
    log(`설정 정리 플러그인을 설치했습니다: ${settings}`);
  }
  const hub = new HermesSocketHub(adapter, () => ({
    change_events: true, heartbeat: true, replay_epoch: 1,
    skin: { branding: { agent_name: "Perdix", prompt_symbol: "❯", welcome: "Perdix",
      goodbye: "Perdix 대화를 저장했습니다.", help_header: "Perdix" },
    banner_logo: PERDIX_LOGO, banner_hero: PERDIX_HERO, colors: {} },
  }), (line) => log(`ws ${line}`));
  const wss = new WebSocketServer({ noServer: true, maxPayload: MAX_PAYLOAD });
  const authorized = (req: IncomingMessage, url: URL): boolean =>
    credentials(req, url).some((candidate) => tokensEqual(candidate, token));

  const server: Server = createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    const key = `${req.method ?? "GET"} ${url.pathname}`;
    if (!PUBLIC_PATHS.has(url.pathname) && !authorized(req, url)) {
      log(`401 ${key}`);
      sendJson(res, 401, { detail: "연결 토큰을 확인하세요" });
      return;
    }
    const route = ROUTES[key];
    if (route === undefined) {
      log(`404 ${key}`);
      sendJson(res, 404, { detail: "Not Found" });
      return;
    }
    readJsonBody(req).then(
      (body) => Promise.resolve(route({ adapter, url, cwd, flows, langflowUi, langflowConfig, relayTokenFile: options.relayTokenFile, desktopConfig, body })),
      (error: unknown) => Promise.reject(error),
    ).then(
      (responseBody) => { log(`200 ${key}`); sendJson(res, 200, responseBody); },
      (error: unknown) => {
        if (error instanceof RouteError) {
          log(`${error.status} ${key} ${error.message}`);
          sendJson(res, error.status, { detail: error.message });
          return;
        }
        log(`500 ${key} ${error instanceof Error ? error.message : String(error)}`);
        sendJson(res, 500, { detail: "Perdix 요청에 실패했습니다" });
      },
    );
  });

  server.on("upgrade", (req: IncomingMessage, socket: Duplex, head: Buffer) => {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    if (url.pathname !== "/api/ws" || !authorized(req, url)) {
      log(`ws rejected ${url.pathname}`);
      socket.write("HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n");
      socket.destroy();
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => {
      log("ws connected");
      hub.attach(ws);
    });
  });

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(options.port ?? 0, "127.0.0.1", () => resolve());
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Hermes 호환 서버 주소를 만들 수 없습니다.");
  return {
    url: `http://127.0.0.1:${address.port}`,
    token,
    close: async () => {
      hub.close();
      for (const client of wss.clients) client.terminate();
      await new Promise<void>((resolve) => wss.close(() => resolve()));
      server.closeAllConnections();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    },
  };
}
