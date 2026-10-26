/**
 * Hermes Desktop 호환 서버. Hermes 데스크톱 앱이 원격 게이트웨이로 접속하면
 * `/api/health`·`/api/status`(HTTP)와 `/api/ws`(JSON-RPC)만으로 Perdix와 대화하게 한다.
 * 대화·도구·권한은 HermesAdapter가 ACP로 Perdix 서버에 넘기고, 여기서는 전송만 맡는다.
 * Hermes 앱은 수정하지 않는다(ADR 0036).
 */
import { randomBytes, timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { Duplex } from "node:stream";
import { WebSocketServer } from "ws";
import type { HermesAdapter } from "./adapter.js";
import { HermesSocketHub } from "./bridge.js";
import { PERDIX_HERO, PERDIX_LOGO } from "./branding.js";
import { VERSION } from "../version.js";

export interface HermesDesktopServer {
  readonly url: string;
  readonly token: string;
  close(): Promise<void>;
}

export interface HermesDesktopServerOptions {
  readonly port?: number;
  readonly cwd?: string;
  readonly token?: string;
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
  readonly adapter: HermesAdapter;
  readonly url: URL;
  readonly cwd: string;
}

type Route = (ctx: RouteContext) => Json | unknown[] | Promise<Json | unknown[]>;

/** 모델 목록을 읽으려면 세션이 하나 필요하다. 처음 필요할 때 한 번만 만든다. */
const bootstrap = new WeakMap<HermesAdapter, Promise<string>>();
async function ensureSession(adapter: HermesAdapter): Promise<string> {
  let pending = bootstrap.get(adapter);
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
  "GET /api/config": () => ({}),
  "GET /api/config/defaults": () => ({}),
  "GET /api/fs/default-cwd": (ctx) => ({ cwd: ctx.cwd, branch: "" }),
  "GET /api/tools/toolsets": () => [],
  "GET /api/tools/terminal/backends": () => ({ active: "local", backends: [] }),
  "GET /api/skills": () => [],
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
    Promise.resolve(route({ adapter, url, cwd })).then(
      (body) => { log(`200 ${key}`); sendJson(res, 200, body); },
      (error: unknown) => {
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
