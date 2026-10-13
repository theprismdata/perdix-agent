import { once } from "node:events";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import { WebSocket } from "ws";
import { z } from "zod";
import { connectToServer } from "../agent/connection.js";
import { spawnServer } from "../agent/spawn.js";
import { FakeSession } from "../ui/test/fake-session.js";
import { HermesAdapter } from "./adapter.js";
import { PERDIX_HERO, PERDIX_LOGO } from "./branding.js";
import { startHermesBridge } from "./bridge.js";
import { approvalOptions } from "./events.js";

const wire = z.object({ id: z.union([z.string(), z.number()]).optional(), method: z.string().optional(),
  params: z.record(z.string(), z.unknown()).optional(), result: z.unknown().optional(), error: z.unknown().optional() });
type Frame = z.infer<typeof wire>;

async function client(url: string) {
  const socket = new WebSocket(url);
  const frames: Frame[] = [];
  const pending = new Map<number, (frame: Frame) => void>();
  let sequence = 0;
  socket.on("message", (data) => {
    const frame = wire.parse(JSON.parse(data.toString()));
    frames.push(frame);
    if (typeof frame.id === "number") {
      pending.get(frame.id)?.(frame);
      pending.delete(frame.id);
    }
  });
  await once(socket, "open");
  return { socket, frames, request: (method: string, params: Record<string, unknown> = {}) => {
    const id = ++sequence;
    return new Promise<Frame>((resolve) => {
      pending.set(id, resolve);
      socket.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
    });
  } };
}

describe("Hermes UI bridge", () => {
  it("completes slash commands over the UI wire without unsupported-request errors", async () => {
    const session = new FakeSession();
    const adapter = new HermesAdapter({ newSession: async () => session, loadSession: async () => session,
      listSessions: async () => [] }, { cwd: session.info.cwd });
    const bridge = await startHermesBridge(adapter);
    const ui = await client(bridge.url);
    try {
      expect(ui.frames).toEqual(expect.arrayContaining([expect.objectContaining({ params: expect.objectContaining({
        type: "gateway.ready", payload: expect.objectContaining({ skin: expect.objectContaining({
          branding: expect.objectContaining({ agent_name: "Perdix" }), banner_logo: PERDIX_LOGO, banner_hero: PERDIX_HERO,
        }) }),
      }) })]));
      const all = await ui.request("complete.slash", { text: "/" });
      expect(all.error).toBeUndefined();
      expect(all.result).toMatchObject({ replace_from: 1, items: expect.arrayContaining([
        { text: "login", display: "/login", meta: "공급자 로그인·API 키 등록", kind: "command" },
      ]) });
      const filtered = await ui.request("complete.slash", { text: "/log" });
      expect(filtered.result).toMatchObject({ items: [expect.objectContaining({ text: "login" }), expect.objectContaining({ text: "logout" })] });
      expect((await ui.request("complete.slash", { text: "/unknown" })).result).toEqual({ items: [], replace_from: 1 });
      expect((await ui.request("complete.slash", { text: "/login openai" })).result).toEqual({ items: [], replace_from: 1 });
    } finally { ui.socket.close(); await bridge.close(); }
  });
  it("refreshes authenticated handles while preserving the selected model and session", async () => {
    const original = new FakeSession({ currentMode: "plan", currentModel: "claude-current", models: [
      { value: "claude-current", name: "Claude" }, { value: "gpt-example", name: "OpenAI" },
    ] });
    const restored = new FakeSession(original.info);
    const loadSession = vi.fn(async () => restored);
    const runAuth = vi.fn(async () => ({ code: 0, provider: "openai" as const }));
    const reconnect = vi.fn(async () => ({ newSession: async () => restored, loadSession, listSessions: async () => [] }));
    const adapter = new HermesAdapter({ newSession: async () => original, loadSession: async () => original, listSessions: async () => [] }, { cwd: "/tmp", runAuth, reconnect });
    await adapter.request("session.create", {});
    const result = await adapter.request("perdix.auth", { session_id: original.info.sessionId, action: "login", provider: "openai" });
    expect(runAuth).toHaveBeenCalledWith("login", "openai", expect.any(AbortSignal));
    expect(loadSession).toHaveBeenCalledWith({ sessionId: original.info.sessionId, cwd: original.info.cwd });
    expect(result).toMatchObject({ output: expect.stringContaining("/model gpt-example") });
    expect((await adapter.request("session.status", { session_id: original.info.sessionId }))).toMatchObject({ output: expect.stringContaining("claude-current") });
    expect(restored.calls).toEqual([]);
    adapter.disconnect();
  });

  it("keeps failed and cancelled authentication from changing the connection", async () => {
    const session = new FakeSession();
    const runAuth = vi.fn().mockResolvedValueOnce({ code: 1 }).mockResolvedValueOnce({ code: 130 });
    const reconnect = vi.fn();
    const adapter = new HermesAdapter({ newSession: async () => session, loadSession: async () => session, listSessions: async () => [] }, { cwd: "/tmp", runAuth, reconnect });
    await adapter.request("session.create", {});
    const params = { session_id: session.info.sessionId, action: "login", provider: "anthropic" };
    expect(await adapter.request("perdix.auth", params)).toMatchObject({ output: expect.stringContaining("실패") });
    expect(await adapter.request("perdix.auth", params)).toMatchObject({ output: "인증을 취소했습니다." });
    expect(reconnect).not.toHaveBeenCalled();
    expect(adapter.authenticating).toBe(false);
    adapter.disconnect();
  });

  it("shows captured status and refuses authentication while a turn runs", async () => {
    const session = new FakeSession();
    const runAuth = vi.fn(async () => ({ code: 0, output: "openai: fake status" }));
    const adapter = new HermesAdapter({ newSession: async () => session, loadSession: async () => session, listSessions: async () => [] }, { cwd: "/tmp", runAuth });
    await adapter.request("session.create", {});
    const params = { session_id: session.info.sessionId, action: "status", provider: "openai" };
    expect(await adapter.request("perdix.auth", params)).toEqual({ output: "openai: fake status" });
    const running = adapter.request("prompt.submit", { session_id: session.info.sessionId, text: "work" });
    await expect(adapter.request("perdix.auth", params)).rejects.toThrow("작업이 끝난");
    session.finishTurn(); await running;
    expect(runAuth).toHaveBeenCalledTimes(1);
    adapter.disconnect();
  });

  it("aborts a pending auth handoff when the UI disconnects", async () => {
    const session = new FakeSession();
    const adapter = new HermesAdapter({ newSession: async () => session, loadSession: async () => session, listSessions: async () => [] }, {
      cwd: "/tmp", runAuth: async (_action, _provider, signal) => new Promise(resolve => signal.addEventListener("abort", () => resolve({ code: 130 }), { once: true })),
    });
    await adapter.request("session.create", {});
    const result = adapter.request("perdix.auth", { session_id: session.info.sessionId, action: "login" });
    expect(adapter.authenticating).toBe(true);
    adapter.disconnect();
    expect(await result).toEqual({ output: "인증을 취소했습니다." });
  });
  it("streams real ACP tool execution and restores persisted history over the UI wire", async () => {
    const dir = mkdtempSync(join(tmpdir(), "perdix-hermes-"));
    const agentsConfig = join(dir, "agents.json");
    writeFileSync(agentsConfig, "{}");
    const serverDir = fileURLToPath(new URL("../../../../server", import.meta.url));
    const server = spawnServer({ command: "uv", source: "flag", args: ["run", "--project", serverDir,
      "perdix-server", "--provider", "fake", "--fake-script", join(serverDir, "tests/fixtures/demo.json"),
      "--agents-config", agentsConfig, "--db", join(dir, "sessions.db")] }, { cwd: dir });
    const connection = await connectToServer(server);
    const adapter = new HermesAdapter(connection, { cwd: dir, mode: "build" });
    const bridge = await startHermesBridge(adapter);
    const ui = await client(bridge.url);
    try {
      const created = await ui.request("session.create");
      const { session_id } = z.object({ session_id: z.string() }).parse(created.result);
      expect((await ui.request("commands.catalog", { session_id })).result).toMatchObject({
        canon: { "/agent": "/agent", "/stop": "/stop" },
        sub: { "/agent": expect.arrayContaining(["chat", "plan", "build"]) },
      });
      expect((await ui.request("slash.exec", { session_id, command: "help" })).result).toMatchObject({ output: expect.stringContaining("/agent") });
      expect((await ui.request("slash.exec", { session_id, command: "usage" })).result).toMatchObject({ output: expect.stringContaining("아직 서버가 보고한") });
      const control = await ui.request("prompt.submit", { session_id, text: "/loop status" });
      expect(control.error).toBeUndefined();
      expect(ui.frames).toContainEqual(expect.objectContaining({ params: expect.objectContaining({
        type: "message.complete", payload: expect.objectContaining({ text: expect.stringContaining("목표 루프가 없습니다") }),
      }) }));
      expect((await ui.request("slash.exec", { session_id, command: "loop 목표\n  들여쓰기를 보존" })).result).toEqual({
        type: "send", message: "/loop 목표\n  들여쓰기를 보존", display: "/loop 목표\n  들여쓰기를 보존",
      });
      expect((await ui.request("slash.exec", { session_id, command: "memories use off" })).result).toEqual({
        type: "send", message: "/memories use off", display: "/memories use off",
      });
      expect((await ui.request("prompt.submit", { session_id, text: "/memories use off" })).error).toBeUndefined();
      const result = await ui.request("prompt.submit", { session_id, text: "한글로 작업 디렉터리를 확인해줘" });
      expect(result.error).toBeUndefined();
      const events = ui.frames.filter((frame) => frame.method === "event").map((frame) => frame.params);
      expect(events).toEqual(expect.arrayContaining([
        expect.objectContaining({ type: "message.delta", payload: expect.objectContaining({ text: expect.any(String) }) }),
        expect.objectContaining({ type: "tool.start" }), expect.objectContaining({ type: "tool.complete" }),
        expect.objectContaining({ type: "message.complete", payload: expect.objectContaining({ status: "complete", text: expect.stringContaining("끝") }) }),
      ]));
      expect((await ui.request("slash.exec", { session_id, command: "agent plan" })).result).toMatchObject({ output: "모드 변경: plan" });
      expect((await ui.request("session.status", { session_id })).result).toMatchObject({ output: expect.stringContaining("모드: plan") });
      expect((await ui.request("model.options", { session_id })).result).toMatchObject({
        provider: "anthropic",
        providers: [
          { slug: "anthropic", name: "Anthropic", is_current: true, models: ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"] },
          { slug: "openai", name: "OpenAI", is_current: false, models: ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna"] },
          { slug: "gemini", name: "Google Gemini", is_current: false, models: ["gemini-3.8-flash"] },
          { slug: "openrouter", name: "OpenRouter", is_current: false, models: ["qwen/qwen3-coder-flash", "openai/gpt-6-luna", "openai/gpt-6.1-sol", "anthropic/claude-sonnet-5.5", "google/gemini-3.8-flash"] },
        ],
      });
      expect((await ui.request("config.set", { session_id, key: "model", value: "gpt-6.1-sol --provider openai" })).error).toBeUndefined();
      expect((await ui.request("model.options", { session_id })).result).toMatchObject({
        provider: "openai", model: "gpt-6.1-sol",
        providers: [ { slug: "anthropic", is_current: false }, { slug: "openai", is_current: true }, { slug: "gemini", is_current: false }, { slug: "openrouter", is_current: false } ],
      });
      expect((await ui.request("model.list", {})).result).toMatchObject({ models: expect.arrayContaining([
        { id: "claude-opus-5-5", name: "claude-opus-5-5", provider: "anthropic" },
        { id: "gpt-6.1-sol", name: "gpt-6.1-sol", provider: "openai" },
        { id: "gemini-3.8-flash", name: "gemini-3.8-flash", provider: "gemini" },
        { id: "qwen/qwen3-coder-flash", name: "qwen/qwen3-coder-flash", provider: "openrouter" },
      ]) });
      expect((await ui.request("config.set", { session_id, key: "model", value: "gemini-3.8-flash" })).error).toBeUndefined();
      expect((await ui.request("model.options", { session_id })).result).toMatchObject({ provider: "gemini", model: "gemini-3.8-flash" });
      expect((await ui.request("config.set", { session_id, key: "model", value: "qwen/qwen3-coder-flash" })).error).toBeUndefined();
      expect((await ui.request("model.options", { session_id })).result).toMatchObject({ provider: "openrouter", model: "qwen/qwen3-coder-flash" });
      expect((await ui.request("config.set", { session_id, key: "model", value: "claude-sonnet-5-5 --provider anthropic" })).error).toBeUndefined();
      expect((await ui.request("slash.exec", { session_id, command: "usage" })).result).toMatchObject({ output: expect.stringContaining("컨텍스트:") });
      expect((await ui.request("slash.exec", { session_id, command: "save" })).result).toMatchObject({ output: expect.stringContaining(`perdix --resume ${session_id}`) });
      const restoredAdapter = new HermesAdapter(connection, { cwd: dir });
      const restored = await restoredAdapter.request("session.resume", { session_id });
      expect(restored).toMatchObject({ session_id, messages: expect.arrayContaining([
        { role: "user", text: "한글로 작업 디렉터리를 확인해줘" },
        expect.objectContaining({ role: "assistant", text: expect.stringContaining("끝") }),
      ]) });
      restoredAdapter.disconnect();
    } finally {
      ui.socket.terminate(); await bridge.close(); await connection.close();
      rmSync(dir, { recursive: true, force: true });
    }
  }, 120_000);

  it("executes a real tool only after the UI returns a supported approval", async () => {
    const dir = mkdtempSync(join(tmpdir(), "perdix-hermes-permission-"));
    const serverDir = resolve(fileURLToPath(new URL("../../../../server", import.meta.url)));
    const script = join(dir, "script.json");
    writeFileSync(script, JSON.stringify([
      { text: "승인이 필요합니다.", tool_uses: [{ id: "bash1", name: "bash", input: { command: "printf HERMES_APPROVED" } }], stop: "tool_use" },
      { text: "승인 후 완료했습니다.", stop: "end_turn" },
    ]));
    const server = spawnServer({ command: "uv", source: "flag", args: ["run", "--project", serverDir,
      "perdix-server", "--provider", "fake", "--fake-script", script, "--db", join(dir, "sessions.db")] }, { cwd: dir });
    const connection = await connectToServer(server);
    const bridge = await startHermesBridge(new HermesAdapter(connection, { cwd: dir, mode: "build" }));
    const ui = await client(bridge.url);
    const approval = new Promise<Frame>((resolve) => {
      ui.socket.on("message", (data) => {
        const frame = wire.parse(JSON.parse(data.toString()));
        if (frame.method === "approval") resolve(frame);
      });
    });
    try {
      const { session_id } = z.object({ session_id: z.string() }).parse((await ui.request("session.create")).result);
      const result = ui.request("prompt.submit", { session_id, text: "승인 후 실행" });
      const request = await approval;
      expect(request.params).toMatchObject({ choices: expect.arrayContaining(["once", "deny"]) });
      expect(ui.frames).not.toEqual(expect.arrayContaining([expect.objectContaining({ params: expect.objectContaining({ type: "tool.complete" }) })]));
      ui.socket.send(JSON.stringify({ jsonrpc: "2.0", id: request.id, result: { choice: "once" } }));
      expect((await result).error).toBeUndefined();
      expect(ui.frames).toEqual(expect.arrayContaining([expect.objectContaining({ params: expect.objectContaining({
        type: "tool.complete", payload: expect.objectContaining({ result_text: expect.stringContaining("HERMES_APPROVED") }),
      }) })]));
    } finally {
      ui.socket.terminate(); await bridge.close(); await connection.close(); rmSync(dir, { recursive: true, force: true });
    }
  }, 120_000);

  it("never turns a session-only UI choice into permanent permission", async () => {
    const session = new FakeSession();
    const adapter = new HermesAdapter({ newSession: async () => session, loadSession: async () => session, listSessions: async () => [] }, { cwd: "/tmp" });
    await adapter.request("session.create", {});
    let id = "";
    adapter.onRequest = (requestId) => { id = requestId; };
    const answer = session.requestPermission({ requestId: "acp1", toolCallId: "tool1", title: "write", options: [
      { optionId: "permanent", name: "항상 허용", kind: "allow_always" }, { optionId: "reject", name: "거절", kind: "reject_once" },
    ] });
    adapter.answerPermission(id, { choice: "session" });
    expect(await answer).toEqual({ cancelled: true });
    adapter.disconnect();
  });

  it("rejects malformed input and unsupported features without prompting the server", async () => {
    const session = new FakeSession();
    const adapter = new HermesAdapter({ newSession: async () => session, loadSession: async () => session, listSessions: async () => [] }, { cwd: "/tmp" });
    const bridge = await startHermesBridge(adapter);
    const ui = await client(bridge.url);
    try {
      expect((await ui.request("prompt.submit", { text: 42 })).error).toMatchObject({ code: -32602 });
      expect((await ui.request("model.save_key", { key: "ignored" })).error).toMatchObject({ code: -32601 });
      expect(session.prompts).toEqual([]);
    } finally { ui.socket.terminate(); await bridge.close(); }
  });

  it("cancels outstanding approvals when the UI disconnects", async () => {
    const session = new FakeSession();
    const adapter = new HermesAdapter({ newSession: async () => session, loadSession: async () => session, listSessions: async () => [] }, { cwd: "/tmp" });
    await adapter.request("session.create", {});
    const answer = session.requestPermission({ requestId: "request", toolCallId: "tool", title: "write", options: [] });
    adapter.disconnect();
    expect(await answer).toEqual({ cancelled: true });
  });

  it("advertises only ACP-supported permission choices", () => {
    expect([...approvalOptions({ requestId: "request", toolCallId: "tool", title: "write", options: [
      { optionId: "once-id", name: "한 번", kind: "allow_once" }, { optionId: "deny-id", name: "거절", kind: "reject_once" },
    ] })]).toEqual([["once", "once-id"], ["deny", "deny-id"]]);
  });

  it("interrupts a running turn and cancels its pending approval", async () => {
    const session = new FakeSession();
    const adapter = new HermesAdapter({ newSession: async () => session, loadSession: async () => session, listSessions: async () => [] }, { cwd: "/tmp" });
    const events: unknown[] = [];
    adapter.onEvent = event => events.push(event);
    await adapter.request("session.create", {});
    const running = adapter.request("prompt.submit", { session_id: session.info.sessionId, text: "취소해줘" });
    const permission = session.requestPermission({ requestId: "request", toolCallId: "tool", title: "write", options: [] });
    await expect(adapter.request("session.create", {})).rejects.toThrow("작업을 취소");
    await expect(adapter.request("slash.exec", { session_id: session.info.sessionId, command: "agent plan" })).rejects.toThrow("작업이 끝난");
    await adapter.request("slash.exec", { session_id: session.info.sessionId, command: "stop" });
    expect(await permission).toEqual({ cancelled: true });
    await running;
    expect(session.calls).toContain("cancel");
    expect(events).toContainEqual(expect.objectContaining({ type: "message.complete", payload: expect.objectContaining({ status: "interrupted" }) }));
    adapter.disconnect();
  });
});
