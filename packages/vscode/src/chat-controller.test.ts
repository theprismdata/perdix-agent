import { describe, expect, it, vi } from "vitest";
import { agent } from "@perdix/protocol";
import { PerdixConnection } from "../../cli/src/agent/connection.js";
import type { AgentEvent, PermissionRequest, PermissionAnswer, SessionHandle } from "../../cli/src/agent/types.js";
import { ChatController, chatMessage } from "./chat-controller.js";

function fixture() {
  let listener: (event: AgentEvent) => void = () => {};
  let permission: (request: PermissionRequest) => Promise<PermissionAnswer> = async () => ({ cancelled: true });
  const session: SessionHandle = {
    info: { sessionId: "s", cwd: "/work", currentMode: "chat", currentModel: "fake", modes: [{ id: "chat", name: "Chat" }], models: [{ value: "fake", name: "Fake" }] },
    prompt: vi.fn(async () => ({ stopReason: "end_turn" })), cancel: vi.fn(async () => {}),
    setMode: vi.fn(async () => {}), setModel: vi.fn(async () => {}), close: vi.fn(async () => {}),
    onEvent: (fn) => { listener = fn; return () => {}; },
    onPermission: (fn) => { permission = fn; return () => {}; },
  };
  const post = vi.fn();
  const connect = vi.fn(async () => session);
  const controller = new ChatController({ connect, post });
  return { controller, session, post, connect, emit: (event: AgentEvent) => listener(event), permission: (req: PermissionRequest) => permission(req) };
}

const permissionRequest: PermissionRequest = { requestId: "r", toolCallId: "t", title: "파일 쓰기", options: [{ optionId: "allow", name: "허용", kind: "allow_once" }] };

describe("Webview chat", () => {
  it("shows the configured agent and model before starting the server", () => {
    const post = vi.fn();
    const initialInfo = { sessionId: "", cwd: "/work", currentMode: "chat", currentModel: "gpt-default",
      modes: [{ id: "chat", name: "기본 에이전트 · Chat" }], models: [{ value: "gpt-default", name: "gpt-default" }] };
    const controller = new ChatController({ connect: vi.fn(), post, initialInfo });
    controller.snapshot();
    expect(post).toHaveBeenCalledWith(expect.objectContaining({ info: initialInfo, connected: false }));
  });

  it("rejects invalid or oversized messages at the host boundary", () => {
    expect(chatMessage.safeParse({ type: "send", text: " " }).success).toBe(false);
    expect(chatMessage.safeParse({ type: "send", text: "a".repeat(100_001) }).success).toBe(false);
    expect(chatMessage.safeParse({ type: "execute", command: "shell" }).success).toBe(false);
    expect(chatMessage.safeParse({ type: "send", text: "hello" }).success).toBe(true);
  });

  it("serializes prompts and preserves streamed events on view recreation", async () => {
    const f = fixture();
    let finish!: () => void;
    vi.mocked(f.session.prompt).mockImplementation(async () => { await new Promise<void>((r) => { finish = r; }); return { stopReason: "end_turn" }; });
    const first = f.controller.send("first");
    await vi.waitFor(() => expect(f.session.prompt).toHaveBeenCalledOnce());
    await f.controller.send("duplicate");
    f.emit({ type: "agent_text", text: "<script>unsafe</script>" });
    f.controller.snapshot();
    expect(f.post).toHaveBeenCalledWith(expect.objectContaining({ type: "snapshot", busy: true, entries: [
      { type: "user_prompt", text: "first" }, { type: "agent_text", text: "<script>unsafe</script>" },
    ] }));
    expect(f.session.prompt).toHaveBeenCalledOnce();
    finish(); await first; await f.controller.dispose();
  });

  it("only accepts advertised permission options and restores pending approvals", async () => {
    const f = fixture(); await f.controller.send("hello");
    const answered = vi.fn();
    const promise = f.permission(permissionRequest).then(answered);
    f.controller.answer("r", "invented");
    await Promise.resolve(); expect(answered).not.toHaveBeenCalled();
    f.controller.snapshot();
    expect(f.post).toHaveBeenCalledWith(expect.objectContaining({ permissions: [permissionRequest] }));
    f.controller.answer("r", "allow"); await promise;
    expect(answered).toHaveBeenCalledWith({ optionId: "allow" });
    await f.controller.dispose();
  });

  it("cancels pending permission requests and sends ACP cancellation", async () => {
    const f = fixture(); await f.controller.send("hello");
    const pending = f.permission(permissionRequest);
    await f.controller.cancel();
    await expect(pending).resolves.toEqual({ cancelled: true });
    expect(f.session.cancel).toHaveBeenCalledOnce();
    await f.controller.dispose();
  });

  it("closes a connection that finishes after disposal without sending a prompt", async () => {
    const f = fixture(); let ready!: (session: SessionHandle) => void;
    f.connect.mockImplementation(() => new Promise((r) => { ready = r; }));
    const sending = f.controller.send("hello");
    await f.controller.dispose(); ready(f.session); await sending;
    expect(f.session.close).toHaveBeenCalledOnce();
    expect(f.session.prompt).not.toHaveBeenCalled();
  });

  it("recovers from connection failures and clears UI only for explicit new chat", async () => {
    const f = fixture(); f.connect.mockRejectedValueOnce(new Error("server missing"));
    await f.controller.send("hello"); expect(f.controller.isBusy).toBe(false);
    expect(f.post).toHaveBeenCalledWith({ type: "event", event: { type: "error", message: "server missing" } });
    await f.controller.send("retry"); expect(f.session.prompt).toHaveBeenCalledWith("retry");
    await f.controller.reset(); expect(f.session.close).toHaveBeenCalledOnce();
    expect(f.post).toHaveBeenCalledWith(expect.objectContaining({ type: "snapshot", entries: [] }));
  });

  it("exchanges real ACP streaming updates and permission replies", async () => {
    const app = agent({ name: "webview-test" })
      .onRequest("initialize", () => ({ protocolVersion: 1, agentCapabilities: {} }))
      .onRequest("session/new", () => ({ sessionId: "s" }))
      .onRequest("session/prompt", async ({ client }) => {
        await client.notify("session/update", { sessionId: "s", update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "ACP reply" } } });
        const answer = await client.request("session/request_permission", { sessionId: "s", toolCall: { toolCallId: "t", title: "edit" }, options: [{ optionId: "deny", name: "거절", kind: "reject_once" }] });
        expect(answer.outcome).toEqual({ outcome: "selected", optionId: "deny" });
        return { stopReason: "end_turn" as const };
      });
    const received: unknown[] = [];
    const controller = new ChatController({
      connect: async () => { const connection = PerdixConnection.open(app); await connection.initialize(); return connection.newSession({ cwd: "/test" }); },
      post: (message) => {
        received.push(message);
        const typed = message as { type: string; request?: PermissionRequest };
        if (typed.type === "permission" && typed.request) controller.answer(typed.request.requestId, "deny");
      },
    });
    await controller.send("hello");
    expect(received).toContainEqual({ type: "event", event: { type: "agent_text", text: "ACP reply" } });
    expect(controller.isBusy).toBe(false);
    await controller.dispose();
  });
});
