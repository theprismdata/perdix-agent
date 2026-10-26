import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { WebSocket } from "ws";
import type { HermesAdapter } from "../hermes/adapter.js";
import { startHermesDesktopServer, type HermesDesktopServer } from "../hermes/desktop-server.js";

type Call = { method: string; params: Record<string, unknown> };

/** 어댑터를 흉내 내는 최소 대역. 서버가 요청을 그대로 넘기고 이벤트를 밀어 보내는지만 본다. */
function fakeAdapter(calls: Call[]) {
  const adapter = {
    onEvent: (() => {}) as (event: { type: string; session_id?: string; payload: Record<string, unknown> }) => void,
    onRequest: (() => {}) as (id: string, method: string, params: Record<string, unknown>) => void,
    disconnect: () => {},
    answerPermission: () => {},
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      if (method === "session.create") return { session_id: "s1" };
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
