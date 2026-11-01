import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { WebSocket } from "ws";
import type { HermesAdapter } from "../hermes/adapter.js";
import { LANGFLOW_PLUGIN_SOURCE } from "../hermes/langflow-plugin.js";
import { WORKFLOW_PLUGIN_SOURCE } from "../hermes/workflow-plugin.js";
import { HIDDEN_SETTINGS_GROUPS, SETTINGS_PLUGIN_SOURCE, hiddenSettingsCss, toMessageTree } from "../hermes/settings-plugin.js";
import { startHermesDesktopServer, type HermesDesktopServer } from "../hermes/desktop-server.js";

type Call = { method: string; params: Record<string, unknown> };

/** 어댑터를 흉내 내는 최소 대역. 서버가 요청을 그대로 넘기고 이벤트를 밀어 보내는지만 본다. */
function fakeAdapter(calls: Call[]) {
  const live = new Set<string>();
  let created = 0;
  const adapter = {
    viewing: undefined as string | undefined,
    savedDefault: undefined as string | undefined,
    setDefaultModel: (model: string) => { adapter.savedDefault = model; },
    live,
    hasSession: (id: string) => live.has(id),
    currentSessionId: () => adapter.viewing,
    onEvent: (() => {}) as (event: { type: string; session_id?: string; payload: Record<string, unknown> }) => void,
    onRequest: (() => {}) as (id: string, method: string, params: Record<string, unknown>) => void,
    disconnect: () => {},
    answerPermission: () => {},
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      if (method === "session.create") { created += 1; const id = `s${created}`; live.add(id); return { session_id: id }; }
      if (method === "session.list") return { sessions: [{ id: "a", title: "첫 대화", preview: "p", started_at: 10 }] };
      if (method === "model.options") return { providers: [], model: "m1", provider: "perdix" };
      if (method === "boom") throw new Error("실패");
      return { echoed: method };
    },
  };
  return adapter as unknown as HermesAdapter & typeof adapter;
}

let server: HermesDesktopServer | undefined;
afterEach(async () => { await server?.close(); server = undefined; });

const auth = (token: string) => ({ Authorization: `Bearer ${token}` });

describe("Hermes Desktop 호환 서버", () => {
  it("Langflow 화면 플러그인: 설정이 있으면 설치하고, 주소와 실제 가동 여부를 알려 준다", async () => {
    const directory = mkdtempSync(join(tmpdir(), "perdix-plugin-"));
    const langflow = createServer((req, res) => { res.writeHead(req.url === "/health" ? 200 : 404); res.end("{}"); });
    await new Promise<void>((resolve) => langflow.listen(0, "127.0.0.1", resolve));
    const port = (langflow.address() as { port: number }).port;
    try {
      const config = join(directory, "langflow.json");
      const flows = { echo: "28549500-69e9-465f-aa8a-4486102151e4" };
      writeFileSync(config, JSON.stringify({ enabled: true, base_url: `http://127.0.0.1:${port}/`, flows }));
      const home = join(directory, "home");
      server = await startHermesDesktopServer(fakeAdapter([]), { token: "t".repeat(32), langflowConfig: config, hermesHome: home });

      const installed = join(home, "desktop-plugins", "langflow", "plugin.js");
      expect(readFileSync(installed, "utf8")).toBe(LANGFLOW_PLUGIN_SOURCE);
      // 편집 화면(webview)은 사이드바와 구조가 섞여 어색해서 뺐다 — 실행(/flow)과 목록(Skills)만 API로 남는다.
      expect(LANGFLOW_PLUGIN_SOURCE).not.toContain("perdix.langflow.open");
      expect(LANGFLOW_PLUGIN_SOURCE).not.toContain("area: 'sidebar.nav'");
      expect(LANGFLOW_PLUGIN_SOURCE).not.toContain("area: 'routes'");
      expect(LANGFLOW_PLUGIN_SOURCE).not.toContain("attachChromeHider");
      expect(LANGFLOW_PLUGIN_SOURCE).not.toMatch(/openWorkspace|statusBar/);
      expect(LANGFLOW_PLUGIN_SOURCE).toContain("area: 'sidebarNav.prefs'");
      expect(LANGFLOW_PLUGIN_SOURCE).toContain("hide: ['messaging', 'artifacts', 'cron']");
      expect(LANGFLOW_PLUGIN_SOURCE).not.toContain("'capabilities'");
      expect(LANGFLOW_PLUGIN_SOURCE).toContain("hideSplashWordmark");
      expect(LANGFLOW_PLUGIN_SOURCE).toContain("watchSplash(ctx)");
      expect(LANGFLOW_PLUGIN_SOURCE).not.toContain("PERDIX");

      const route = `${server.url}/api/plugins/langflow/config`;
      expect((await fetch(route)).status).toBe(401);
      const up = await (await fetch(route, { headers: auth(server.token) })).json();
      expect(up).toEqual({ url: `http://127.0.0.1:${port}`, ready: true });

      await new Promise<void>((resolve) => langflow.close(() => resolve()));
      const down = await (await fetch(route, { headers: auth(server.token) })).json();
      expect(down).toEqual({ url: `http://127.0.0.1:${port}`, ready: false });
    } finally {
      langflow.closeAllConnections();
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("Langflow 설정이 없거나 꺼져 있으면 플러그인을 설치하지 않고 url을 비운다", async () => {
    const directory = mkdtempSync(join(tmpdir(), "perdix-plugin-off-"));
    try {
      const home = join(directory, "home");
      server = await startHermesDesktopServer(fakeAdapter([]), { token: "t".repeat(32), hermesHome: home });
      // Workflow 플러그인은 hermesHome만 있어도 설치되므로(별도 테스트) desktop-plugins 자체는 생긴다 —
      // 여기서 보는 건 Langflow 설정이 없을 때 langflow 서브디렉터리가 안 생기는지다.
      expect(existsSync(join(home, "desktop-plugins", "langflow"))).toBe(false);
      const body = await (await fetch(`${server.url}/api/plugins/langflow/config`, { headers: auth(server.token) })).json();
      expect(body).toEqual({ url: null, ready: false });
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("Workflow 플러그인: hermesHome만 있으면 설치하고, 역할 목록과 꺼진 Langflow 주소를 돌려준다", async () => {
    const directory = mkdtempSync(join(tmpdir(), "perdix-workflow-"));
    try {
      const home = join(directory, "home");
      server = await startHermesDesktopServer(fakeAdapter([]), { token: "t".repeat(32), hermesHome: home });

      const installed = join(home, "desktop-plugins", "workflow", "plugin.js");
      expect(readFileSync(installed, "utf8")).toBe(WORKFLOW_PLUGIN_SOURCE);
      // 설정 정리 플러그인도 함께 설치된다.
      const settingsPlugin = join(home, "desktop-plugins", "perdix-settings", "plugin.js");
      expect(readFileSync(settingsPlugin, "utf8")).toBe(SETTINGS_PLUGIN_SOURCE);
      expect(WORKFLOW_PLUGIN_SOURCE).toContain("area: 'sidebar.nav'");
      expect(WORKFLOW_PLUGIN_SOURCE).toContain("area: 'routes'");
      // 공식 SDK 밖의 window-open 경로는 Hermes가 무조건 거부한다 — 안 씀.
      expect(WORKFLOW_PLUGIN_SOURCE).not.toContain("window.hermesDesktop");
      expect(WORKFLOW_PLUGIN_SOURCE).not.toContain('target="_blank"');
      expect(WORKFLOW_PLUGIN_SOURCE).not.toContain("window.open");

      const headers = auth(server.token);
      const roles = await (await fetch(`${server.url}/api/plugins/workflow/roles`, { headers })).json() as Array<Record<string, unknown>>;
      expect(roles).toHaveLength(10);
      expect(roles[0]).toMatchObject({ id: "general", readOnly: true });

      const langflow = await (await fetch(`${server.url}/api/plugins/workflow/langflow-url`, { headers })).json();
      expect(langflow).toEqual({ url: null, ready: false });
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("Workflow 노드 에디터: Langflow 설정이 없으면 모든 langflow/* 경로가 disabled를 돌려준다", async () => {
    server = await startHermesDesktopServer(fakeAdapter([]), { token: "t".repeat(32) });
    const headers = auth(server.token);
    for (const path of ["flows", "flow?id=x", "components"]) {
      const body = await (await fetch(`${server.url}/api/plugins/workflow/langflow/${path}`, { headers })).json();
      expect(body).toEqual({ ok: false, error: "disabled" });
    }
    const save = await (await fetch(`${server.url}/api/plugins/workflow/langflow/flow?id=x`, {
      method: "PATCH", headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ data: { nodes: [], edges: [] } }),
    })).json();
    expect(save).toEqual({ ok: false, error: "disabled" });
    const create = await (await fetch(`${server.url}/api/plugins/workflow/langflow/flow`, {
      method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ name: "새 플로우" }),
    })).json();
    expect(create).toEqual({ ok: false, error: "disabled" });
  });

  it("Workflow 노드 에디터: 플로우 목록·조회·저장, 컴포넌트 카탈로그를 Langflow API로 중계한다", async () => {
    const directory = mkdtempSync(join(tmpdir(), "perdix-langflow-editor-"));
    const calls: Array<{ method: string; url: string; apiKey: string | undefined; body: unknown }> = [];
    const flowData = { nodes: [{ id: "n1", type: "genericNode", position: { x: 1, y: 2 }, data: {} }], edges: [] };
    const langflow = createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on("data", (chunk) => chunks.push(chunk));
      req.on("end", () => {
        const bodyText = Buffer.concat(chunks).toString("utf8");
        calls.push({
          method: req.method ?? "GET", url: req.url ?? "", apiKey: req.headers["x-api-key"] as string | undefined,
          body: bodyText ? JSON.parse(bodyText) : undefined,
        });
        res.setHeader("content-type", "application/json");
        if (req.url === "/api/v1/flows/?remove_example_flows=true") {
          res.end(JSON.stringify([{ id: "f1", name: "플로우1", description: "설명" }]));
        } else if (req.url === "/api/v1/flows/f1" && req.method === "GET") {
          res.end(JSON.stringify({ id: "f1", name: "플로우1", description: "설명", data: flowData }));
        } else if (req.url === "/api/v1/flows/" && req.method === "POST") {
          const body = JSON.parse(bodyText) as { name: string; description?: string };
          res.writeHead(201); res.end(JSON.stringify({ id: "f2", name: body.name, description: body.description ?? "" }));
        } else if (req.url === "/api/v1/flows/f1" && req.method === "PATCH") {
          res.end(JSON.stringify({ id: "f1", name: "플로우1", description: "설명" }));
        } else if ((req.url === "/api/v1/flows/f1" || req.url === "/api/v1/flows/f9") && req.method === "DELETE") {
          res.end(JSON.stringify({ message: "Flow deleted successfully" }));
        } else if (req.url === "/api/v1/all") {
          res.end(JSON.stringify({ inputs: { ChatInput: { display_name: "Chat Input" } } }));
        } else {
          res.writeHead(404); res.end("{}");
        }
      });
    });
    await new Promise<void>((resolve) => langflow.listen(0, "127.0.0.1", resolve));
    const port = (langflow.address() as { port: number }).port;
    try {
      const config = join(directory, "langflow.json");
      const keyFile = join(directory, "api-key");
      writeFileSync(keyFile, "secret-key-123\n");
      writeFileSync(config, JSON.stringify({
        enabled: true, base_url: `http://127.0.0.1:${port}`, api_key_file: keyFile,
        flows: { coder: "f1", other: "f3" },
      }));
      server = await startHermesDesktopServer(fakeAdapter([]), { token: "t".repeat(32), langflowConfig: config });
      const headers = { ...auth(server.token), "Content-Type": "application/json" };

      const flows = await (await fetch(`${server.url}/api/plugins/workflow/langflow/flows`, { headers })).json();
      expect(flows).toEqual({ ok: true, result: [{ id: "f1", name: "플로우1", description: "설명" }] });

      const flow = await (await fetch(`${server.url}/api/plugins/workflow/langflow/flow?id=f1`, { headers })).json();
      expect(flow).toEqual({ ok: true, result: { id: "f1", name: "플로우1", description: "설명", data: flowData } });

      const components = await (await fetch(`${server.url}/api/plugins/workflow/langflow/components`, { headers })).json();
      expect(components).toEqual({ ok: true, result: { inputs: { ChatInput: { display_name: "Chat Input" } } } });

      const newData = { nodes: [{ id: "n1", type: "genericNode", position: { x: 9, y: 9 }, data: {} }], edges: [] };
      const save = await (await fetch(`${server.url}/api/plugins/workflow/langflow/flow?id=f1`, {
        method: "PATCH", headers, body: JSON.stringify({ data: newData }),
      })).json();
      expect(save).toEqual({ ok: true, result: { saved: true } });

      const create = await (await fetch(`${server.url}/api/plugins/workflow/langflow/flow`, {
        method: "POST", headers, body: JSON.stringify({ name: "  새 플로우  " }),
      })).json();
      expect(create).toEqual({ ok: true, result: { id: "f2", name: "새 플로우", description: "" } });
      const createCall = calls.find((call) => call.method === "POST");
      expect(createCall?.body).toEqual({ name: "새 플로우", description: "", data: { nodes: [], edges: [], viewport: { x: 0, y: 0, zoom: 1 } } });
      const badCreate = await (await fetch(`${server.url}/api/plugins/workflow/langflow/flow`, {
        method: "POST", headers, body: JSON.stringify({ name: "   " }),
      })).json();
      expect(badCreate).toEqual({ ok: false, error: "invalid_request" });

      // 삭제: 이 플로우를 가리키던 채팅 별칭을 알려 준다(설정 파일은 고치지 않는다).
      const remove = await (await fetch(`${server.url}/api/plugins/workflow/langflow/flow?id=f1`, {
        method: "DELETE", headers,
      })).json();
      expect(remove).toEqual({ ok: true, result: { id: "f1", aliases: ["coder"] } });
      const removeUnaliased = await (await fetch(`${server.url}/api/plugins/workflow/langflow/flow?id=f9`, {
        method: "DELETE", headers,
      })).json();
      expect(removeUnaliased).toEqual({ ok: true, result: { id: "f9", aliases: [] } });
      expect(calls.filter((call) => call.method === "DELETE").map((call) => call.url))
        .toEqual(["/api/v1/flows/f1", "/api/v1/flows/f9"]);
      const missingId = await (await fetch(`${server.url}/api/plugins/workflow/langflow/flow`, {
        method: "DELETE", headers,
      })).json();
      expect(missingId).toEqual({ ok: false, error: "missing_id" });
      const missing = await (await fetch(`${server.url}/api/plugins/workflow/langflow/flow?id=nope`, {
        method: "DELETE", headers,
      })).json();
      expect(missing).toMatchObject({ ok: false, error: "request_failed" });

      expect(calls.every((call) => call.apiKey === "secret-key-123")).toBe(true);
      const patchCall = calls.find((call) => call.method === "PATCH");
      expect(patchCall?.body).toEqual({ data: newData });
    } finally {
      langflow.closeAllConnections();
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("Workflow 노드 에디터: Perdix Agent Model 칸 목록을 loopback 중계에서만 토큰을 붙여 받아 온다", async () => {
    const directory = mkdtempSync(join(tmpdir(), "perdix-relay-models-"));
    const seen: Array<{ url: string; auth: string | undefined }> = [];
    const relay = createServer((req, res) => {
      seen.push({ url: req.url ?? "", auth: req.headers.authorization });
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify({ object: "list", data: [{ id: "openai/gpt-6-luna" }, { id: "anthropic/claude-sonnet-5.5" }, { nope: 1 }] }));
    });
    await new Promise<void>((resolve) => relay.listen(0, "127.0.0.1", resolve));
    const port = (relay.address() as { port: number }).port;
    try {
      const tokenFile = join(directory, "relay-token");
      writeFileSync(tokenFile, "relay-secret\n");
      server = await startHermesDesktopServer(fakeAdapter([]), { token: "t".repeat(32), relayTokenFile: tokenFile });
      const headers = auth(server.token);
      const get = async (query: string) =>
        (await fetch(`${server!.url}/api/plugins/workflow/relay/models${query}`, { headers })).json();

      expect(await get(`?url=${encodeURIComponent(`http://127.0.0.1:${port}/`)}`))
        .toEqual({ ok: true, result: ["openai/gpt-6-luna", "anthropic/claude-sonnet-5.5"] });
      expect(seen).toEqual([{ url: "/v1/models", auth: "Bearer relay-secret" }]);

      expect(await get("")).toEqual({ ok: false, error: "missing_url" });
      // loopback이 아닌 주소로는 토큰을 보내지 않는다.
      expect(await get(`?url=${encodeURIComponent("http://example.com:18797")}`))
        .toMatchObject({ ok: false, error: "relay_failed" });
      expect(seen).toHaveLength(1);
      // 중계가 꺼져 있으면 오류로 알린다.
      relay.closeAllConnections();
      await new Promise<void>((resolve) => relay.close(() => resolve()));
      expect(await get(`?url=${encodeURIComponent(`http://127.0.0.1:${port}`)}`))
        .toMatchObject({ ok: false, error: "relay_failed" });
    } finally {
      relay.closeAllConnections();
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("설정 정리: 연결 안 된 메뉴 묶음과 하위 항목만 감추고, Model·Appearance 등은 남긴다", () => {
    const css = hiddenSettingsCss();
    for (const id of ["billing", "vault", "config:voice", "config:advanced", "providers", "about"]) {
      expect(HIDDEN_SETTINGS_GROUPS).toContain(id);
      expect(css).toContain(`[data-tour="nav-${id}"]`);
      expect(css).toContain(`[data-tour^="nav-${id}:"]`);
    }
    for (const id of ["config:model:fallbacks", "config:model:auxiliary", "config:model:moa", "config:appearance:pet"]) {
      expect(css).toContain(`[data-tour="nav-${id}"]`);
    }
    for (const kept of ["config:model", "config:appearance", "notifications", "gateway", "keybinds", "plugins"]) {
      expect(css).not.toContain(`[data-tour="nav-${kept}"]`);
      expect(css).not.toContain(`[data-tour^="nav-${kept}:"]`);
    }
    expect(css).not.toContain(`"nav-config:model:main"`);
    expect(SETTINGS_PLUGIN_SOURCE).toContain("export default");
    expect(SETTINGS_PLUGIN_SOURCE).toContain("perdix-settings-hidden");
    // 한국어 팩을 앱에 직접 등록한다(원격 모드에서 서버 언어 팩 동기화가 돌지 않을 때가 있다).
    expect(SETTINGS_PLUGIN_SOURCE).toContain("registerAppLocale('ko'");
    // 배열 문구(입력창 안내 등)까지 받도록 트리로 등록한다. 점이 든 키 이름은 쪼개지 않는다.
    const tree = toMessageTree({
      "settings.sections.model": "모델",
      "composer.newSessionPlaceholders": ["무엇을 만들어 볼까요?"],
      "keybinds.actions.session.slot.1": "최근 대화 1로 이동",
      "composer.hotkeyDescs.composer.help": "빠른 도움말",
    });
    expect(tree).toEqual({
      settings: { sections: { model: "모델" } },
      composer: { newSessionPlaceholders: ["무엇을 만들어 볼까요?"], hotkeyDescs: { "composer.help": "빠른 도움말" } },
      keybinds: { actions: { "session.slot.1": "최근 대화 1로 이동" } },
    });
    expect(SETTINGS_PLUGIN_SOURCE).toContain('"newSessionPlaceholders":["무엇을 만들어 볼까요?"');
  });

  it("Main model: 빈 설정 목록을 주고, 저장하면 general 역할 할당과 현재 대화 모델을 바꾼다", async () => {
    const calls: Call[] = [];
    const adapter = fakeAdapter(calls);
    server = await startHermesDesktopServer(adapter, { token: "t".repeat(32) });
    const headers = { ...auth(server.token), "Content-Type": "application/json" };
    const post = (body: unknown) => fetch(`${server!.url}/api/model/set`, { method: "POST", headers, body: JSON.stringify(body) });

    expect(await (await fetch(`${server.url}/api/config/schema`, { headers })).json()).toEqual({ fields: {}, category_order: [] });
    expect(await (await fetch(`${server.url}/api/model/auxiliary`, { headers })).json())
      .toEqual({ main: { model: "m1", provider: "perdix" }, tasks: [] });
    expect(await (await fetch(`${server.url}/api/model/moa`, { headers })).json())
      .toEqual({ default_preset: "", active_preset: "", presets: {} });
    calls.length = 0;

    adapter.viewing = "chat-1";
    const ok = await post({ scope: "main", provider: "openrouter", model: "openai/gpt-6.1-sol" });
    expect(ok.status).toBe(200);
    expect(await ok.json()).toEqual({ ok: true, model: "openai/gpt-6.1-sol", provider: "openrouter" });
    expect(adapter.savedDefault).toBe("openai/gpt-6.1-sol");
    expect(calls.filter((call) => call.method !== "session.create")).toEqual([
      { method: "slash.exec", params: { session_id: "s1", command: "/assign general openai/gpt-6.1-sol" } },
      { method: "config.set", params: { session_id: "s1", key: "model", value: "openai/gpt-6.1-sol" } },
      { method: "config.set", params: { session_id: "chat-1", key: "model", value: "openai/gpt-6.1-sol" } },
    ]);

    const aux = await post({ scope: "auxiliary", provider: "openrouter", model: "x/y", task: "vision" });
    expect(aux.status).toBe(400);
    expect(await aux.json()).toEqual({ detail: "Perdix에서는 Main model만 바꿀 수 있습니다." });
    const direct = await post({ scope: "main", provider: "anthropic", model: "claude-opus-5-5" });
    expect(direct.status).toBe(400);
    expect(((await direct.json()) as { detail: string }).detail).toContain("OpenRouter");
    const empty = await post({ scope: "main", provider: "openrouter", model: " " });
    expect(empty.status).toBe(400);
  });

  it("화면 설정 기록: 언어 저장을 받아 HERMES_HOME에 보관하고 다시 띄워도 돌려준다", async () => {
    const directory = mkdtempSync(join(tmpdir(), "perdix-desktop-config-"));
    try {
      const home = join(directory, "home");
      server = await startHermesDesktopServer(fakeAdapter([]), { token: "t".repeat(32), hermesHome: home });
      const headers = { ...auth(server.token), "Content-Type": "application/json" };
      const put = (body: unknown, query = "") => fetch(`${server!.url}/api/config${query}`, { method: "PUT", headers, body: JSON.stringify(body) });

      expect(await (await fetch(`${server.url}/api/config`, { headers })).json()).toEqual({});
      expect(await (await put({ config: { display: { language: "en" } } })).json()).toEqual({ ok: true });
      // Hermes는 언어를 고를 때 preserve_language=true를 붙여 보낸다. 언어를 저장하고, 빠진 키는 그대로 둔다.
      await put({ config: { display: { show_reasoning: true } } }, "?preserve_language=true");
      await put({ config: { display: { language: "ko" } } }, "?preserve_language=true");
      expect(await (await fetch(`${server.url}/api/config`, { headers })).json())
        .toEqual({ display: { language: "ko", show_reasoning: true } });
      expect((await put({ nope: 1 })).status).toBe(400);

      await server.close();
      server = await startHermesDesktopServer(fakeAdapter([]), { token: "t".repeat(32), hermesHome: home });
      expect(await (await fetch(`${server.url}/api/config`, { headers: auth(server.token) })).json())
        .toEqual({ display: { language: "ko", show_reasoning: true } });
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("모델 정보용 임시 세션이 사라지면(Perdix 서버 재시작) 새로 만든다", async () => {
    const calls: Call[] = [];
    const adapter = fakeAdapter(calls);
    server = await startHermesDesktopServer(adapter, { token: "t".repeat(32) });
    const headers = auth(server.token);
    const info = async () => (await fetch(`${server!.url}/api/model/info`, { headers })).status;
    expect(await info()).toBe(200);
    expect(await info()).toBe(200);
    expect(calls.filter((call) => call.method === "session.create")).toHaveLength(1);
    adapter.live.clear();
    expect(await info()).toBe(200);
    const creates = calls.filter((call) => call.method === "session.create");
    expect(creates).toHaveLength(2);
    expect(calls.filter((call) => call.method === "model.options").at(-1)?.params).toEqual({ session_id: "s2" });
  });

  it("Langflow 설정이 있으면 플로우를 스킬 목록에 보여주고, 꺼져 있거나 없으면 비운다", async () => {
    const directory = mkdtempSync(join(tmpdir(), "perdix-flows-"));
    try {
      const path = join(directory, "langflow.json");
      const flows = { echo: "28549500-69e9-465f-aa8a-4486102151e4" };
      writeFileSync(path, JSON.stringify({ enabled: true, flows, flow_descriptions: { echo: "에코" } }));
      server = await startHermesDesktopServer(fakeAdapter([]), { token: "t".repeat(32), langflowConfig: path });
      const skills = await (await fetch(`${server.url}/api/skills`, { headers: auth(server.token) })).json() as Array<Record<string, unknown>>;
      expect(skills).toHaveLength(1);
      expect(skills[0]).toMatchObject({ name: "echo", category: "Langflow", enabled: true });
      expect(String(skills[0]?.description)).toContain("/flow echo <입력>");

      writeFileSync(path, JSON.stringify({ enabled: false, flows }));
      expect(await (await fetch(`${server.url}/api/skills`, { headers: auth(server.token) })).json()).toEqual([]);
      writeFileSync(path, "{깨진 json");
      expect(await (await fetch(`${server.url}/api/skills`, { headers: auth(server.token) })).json()).toEqual([]);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("health와 status는 토큰 없이, 나머지는 토큰이 있어야 응답한다", async () => {
    server = await startHermesDesktopServer(fakeAdapter([]), { token: "t".repeat(32), cwd: "/work" });
    expect((await fetch(`${server.url}/api/health`)).status).toBe(200);
    const status = await (await fetch(`${server.url}/api/status`)).json() as { gateway_running: boolean };
    expect(status.gateway_running).toBe(true);
    expect((await fetch(`${server.url}/api/profiles`)).status).toBe(401);
    expect((await fetch(`${server.url}/api/profiles`, { headers: auth("wrong") })).status).toBe(401);
    expect((await fetch(`${server.url}/api/profiles`, { headers: auth(server.token) })).status).toBe(200);
  });

  it("모르는 경로는 404, Perdix 데이터는 어댑터에서 가져온다", async () => {
    const calls: Call[] = [];
    server = await startHermesDesktopServer(fakeAdapter(calls), { token: "t".repeat(32), cwd: "/work" });
    const headers = auth(server.token);
    expect((await fetch(`${server.url}/api/nope`, { headers })).status).toBe(404);
    const page = await (await fetch(`${server.url}/api/profiles/sessions`, { headers })).json() as
      { total: number; sessions: Array<{ id: string; title: string; cwd: string }> };
    expect(page.total).toBe(1);
    expect(page.sessions[0]).toMatchObject({ id: "a", title: "첫 대화", cwd: "/work" });
    const info = await (await fetch(`${server.url}/api/model/info`, { headers })).json();
    expect(info).toEqual({ model: "m1", provider: "perdix" });
    // 모델 목록용 세션은 한 번만 만든다.
    await fetch(`${server.url}/api/model/options`, { headers });
    expect(calls.filter((call) => call.method === "session.create")).toHaveLength(1);
  });

  it("WebSocket은 토큰 없이는 거부하고, 연결되면 ready를 보낸 뒤 RPC를 어댑터에 넘긴다", async () => {
    const calls: Call[] = [];
    const adapter = fakeAdapter(calls);
    server = await startHermesDesktopServer(adapter, { token: "t".repeat(32) });
    const base = server.url.replace("http", "ws");

    await new Promise<void>((resolve) => {
      const rejected = new WebSocket(`${base}/api/ws`);
      rejected.on("error", () => resolve());
      rejected.on("open", () => { rejected.close(); resolve(); throw new Error("토큰 없이 연결됐습니다"); });
    });

    const socket = new WebSocket(`${base}/api/ws?token=${server.token}`);
    type Frame = { id?: number; method?: string; result?: unknown; error?: unknown; params?: { type?: string; payload?: Record<string, unknown> } };
    const frames: Frame[] = [];
    socket.on("message", (data) => frames.push(JSON.parse(data.toString()) as Frame));
    await new Promise<void>((resolve) => socket.on("open", () => resolve()));
    await expect.poll(() => frames.length).toBeGreaterThan(0);
    expect(frames[0]).toMatchObject({ method: "event", params: { type: "gateway.ready" } });
    expect(frames[0]?.params?.payload).toMatchObject({ change_events: true, heartbeat: true });

    socket.send(JSON.stringify({ jsonrpc: "2.0", id: 7, method: "prompt.submit", params: { text: "hi" } }));
    await expect.poll(() => frames.find((frame) => frame.id === 7)).toMatchObject({ result: { echoed: "prompt.submit" } });
    expect(calls.at(-1)).toEqual({ method: "prompt.submit", params: { text: "hi" } });

    socket.send(JSON.stringify({ jsonrpc: "2.0", id: 8, method: "boom", params: {} }));
    await expect.poll(() => frames.find((frame) => frame.id === 8)).toMatchObject({ error: { message: "실패" } });

    adapter.onEvent({ type: "message.delta", session_id: "s1", payload: { text: "안" } });
    await expect.poll(() => frames.find((frame) => frame.params?.type === "message.delta")).toBeTruthy();
    socket.close();
  });
});
