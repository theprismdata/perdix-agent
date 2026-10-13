import { agent, type AgentContext, type SessionUpdate } from "@perdix/protocol";
import { describe, expect, it } from "vitest";
import { PerdixConnection } from "./connection.js";
import type { AgentEvent } from "./types.js";

const SID = "sess-1";

const modelOptions = (current: string) => [
  {
    id: "model",
    name: "Model",
    category: "model",
    type: "select" as const,
    currentValue: current,
    options: [
      { value: "fake-1", name: "fake-1" },
      { value: "fake-2", name: "fake-2" },
    ],
  },
];

const turnUpdates: SessionUpdate[] = [
  { sessionUpdate: "agent_thought_chunk", content: { type: "text", text: "thinking" } },
  { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "hello" } },
  {
    sessionUpdate: "tool_call",
    toolCallId: "t1",
    title: "edit a.txt",
    kind: "edit",
    status: "pending",
    rawInput: { path: "a.txt" },
    _meta: { perdix: { parentToolCallId: "p0" } },
  },
  {
    sessionUpdate: "tool_call_update",
    toolCallId: "t1",
    status: "completed",
    content: [
      { type: "diff", path: "a.txt", oldText: null, newText: "x\n" },
      { type: "content", content: { type: "text", text: "ok" } },
    ],
  },
  {
    sessionUpdate: "plan",
    entries: [{ content: "step", status: "pending", priority: "high" }],
  },
  { sessionUpdate: "usage_update", used: 10, size: 100, cost: { amount: 0.5, currency: "USD" } },
  { sessionUpdate: "current_mode_update", currentModeId: "build" },
  { sessionUpdate: "config_option_update", configOptions: modelOptions("fake-2") },
  { sessionUpdate: "session_info_update", title: "ignored" },
];

/** in-process 가짜 에이전트: SDK agent() 앱으로 만든다. */
function fakeAgent(record: { cancelled: string[]; permission: unknown[]; setMode: string[]; setModel: unknown[] }) {
  const send = (client: AgentContext, update: SessionUpdate) =>
    client.notify("session/update", { sessionId: SID, update });
  return agent({ name: "fake" })
    .onRequest("initialize", () => ({ protocolVersion: 1, agentCapabilities: { loadSession: true } }))
    .onRequest("session/new", () => ({
      sessionId: SID,
      modes: {
        currentModeId: "chat",
        availableModes: [
          { id: "chat", name: "Chat" },
          { id: "build", name: "Build", description: "write" },
        ],
      },
      configOptions: modelOptions("fake-1"),
    }))
    .onRequest("session/load", async ({ params, client }) => {
      await client.notify("session/update", {
        sessionId: params.sessionId,
        update: { sessionUpdate: "user_message_chunk", content: { type: "text", text: "earlier" } },
      });
      return { modes: { currentModeId: "plan", availableModes: [{ id: "plan", name: "Plan" }] } };
    })
    .onRequest("session/list", ({ params }) => ({
      sessions: [{ sessionId: SID, cwd: params.cwd ?? "/", title: "t", updatedAt: "2026-01-01T00:00:00Z" }],
    }))
    .onRequest("session/set_mode", ({ params }) => {
      record.setMode.push(params.modeId);
      return {};
    })
    .onRequest("session/set_config_option", ({ params }) => {
      record.setModel.push(params);
      return { configOptions: modelOptions(String((params as { value: unknown }).value)) };
    })
    .onNotification("session/cancel", ({ params }) => {
      record.cancelled.push(params.sessionId);
    })
    .onRequest("session/prompt", async ({ params, client }) => {
      const text = params.prompt[0]?.type === "text" ? params.prompt[0].text : "";
      if (text === "permission") {
        const res = await client.request("session/request_permission", {
          sessionId: SID,
          toolCall: { toolCallId: "t9", title: "bash rm", kind: "execute", rawInput: { cmd: "rm" } },
          options: [
            { optionId: "allow_once", name: "Allow", kind: "allow_once" },
            { optionId: "reject_once", name: "Reject", kind: "reject_once" },
          ],
        });
        record.permission.push(res.outcome);
        return { stopReason: "end_turn" };
      }
      if (text === "boom") throw new Error("kaboom");
      for (const u of turnUpdates) await send(client, u);
      return { stopReason: "end_turn", _meta: { perdix: { verification: "passed" } } };
    });
}

async function setup() {
  const record = { cancelled: [] as string[], permission: [] as unknown[], setMode: [] as string[], setModel: [] as unknown[] };
  const connection = PerdixConnection.open(fakeAgent(record));
  await connection.initialize();
  return { connection, record };
}

describe("AgentSession", () => {
  it("keeps turn lifecycle untouched when querying loop status", async () => {
    const { connection } = await setup();
    const session = await connection.newSession({ cwd: "/w" });
    const events: AgentEvent[] = [];
    session.onEvent((event) => events.push(event));
    await session.prompt("/loop status");
    expect(events.some((event) => event.type === "turn_started" || event.type === "turn_ended")).toBe(false);
    await session.close();
  });
  it("builds SessionInfo from session/new", async () => {
    const { connection } = await setup();
    const session = await connection.newSession({ cwd: "/w" });
    expect(session.info).toEqual({
      sessionId: SID,
      cwd: "/w",
      currentMode: "chat",
      modes: [
        { id: "chat", name: "Chat", description: null },
        { id: "build", name: "Build", description: "write" },
      ],
      currentModel: "fake-1",
      models: [
        { value: "fake-1", name: "fake-1" },
        { value: "fake-2", name: "fake-2" },
      ],
    });
    await session.close();
  });

  it("maps SessionUpdate to AgentEvent in order", async () => {
    const { connection } = await setup();
    const session = await connection.newSession({ cwd: "/w" });
    const events: AgentEvent[] = [];
    session.onEvent((e) => events.push(e));
    const result = await session.prompt("go");
    expect(result).toEqual({ stopReason: "end_turn", verification: "passed" });
    expect(events).toEqual([
      { type: "turn_started" },
      { type: "agent_thought", text: "thinking" },
      { type: "agent_text", text: "hello" },
      {
        type: "tool_call",
        toolCallId: "t1",
        title: "edit a.txt",
        kind: "edit",
        status: "pending",
        rawInput: { path: "a.txt" },
        parentToolCallId: "p0",
      },
      {
        type: "tool_call_update",
        toolCallId: "t1",
        status: "completed",
        content: [
          { type: "diff", path: "a.txt", oldText: null, newText: "x\n" },
          { type: "text", text: "ok" },
        ],
      },
      { type: "plan", entries: [{ content: "step", status: "pending", priority: "high" }] },
      { type: "usage", used: 10, size: 100, cost: 0.5 },
      { type: "mode_changed", modeId: "build" },
      { type: "model_changed", model: "fake-2" },
      { type: "turn_ended", stopReason: "end_turn", verification: "passed" },
    ]);
    expect(session.info.currentMode).toBe("build");
    expect(session.info.currentModel).toBe("fake-2");
    await session.close();
  });

  it("emits error and turn_ended when the prompt fails", async () => {
    const { connection } = await setup();
    const session = await connection.newSession({ cwd: "/w" });
    const events: AgentEvent[] = [];
    session.onEvent((e) => events.push(e));
    const result = await session.prompt("boom");
    expect(result.stopReason).toBe("error");
    expect(events.map((e) => e.type)).toEqual(["turn_started", "error", "turn_ended"]);
    await session.close();
  });

  it("answers permission requests with reject_once when no handler is registered", async () => {
    const { connection, record } = await setup();
    const session = await connection.newSession({ cwd: "/w" });
    await session.prompt("permission");
    expect(record.permission).toEqual([{ outcome: "selected", optionId: "reject_once" }]);
    await session.close();
  });

  it("routes permission requests to the registered handler", async () => {
    const { connection, record } = await setup();
    const session = await connection.newSession({ cwd: "/w" });
    const seen: unknown[] = [];
    session.onPermission(async (req) => {
      seen.push({ ...req, requestId: typeof req.requestId });
      return { optionId: "allow_once" };
    });
    await session.prompt("permission");
    expect(seen).toEqual([
      {
        requestId: "string",
        toolCallId: "t9",
        title: "bash rm",
        kind: "execute",
        rawInput: { cmd: "rm" },
        options: [
          { optionId: "allow_once", name: "Allow", kind: "allow_once" },
          { optionId: "reject_once", name: "Reject", kind: "reject_once" },
        ],
      },
    ]);
    expect(record.permission).toEqual([{ outcome: "selected", optionId: "allow_once" }]);

    session.onPermission(async () => ({ cancelled: true }));
    await session.prompt("permission");
    expect(record.permission[1]).toEqual({ outcome: "cancelled" });
    await session.close();
  });

  it("sends cancel, set_mode and set_config_option(model)", async () => {
    const { connection, record } = await setup();
    const session = await connection.newSession({ cwd: "/w" });
    await session.setMode("build");
    await session.setModel("fake-2");
    await session.cancel();
    await new Promise((r) => setTimeout(r, 10));
    expect(record.setMode).toEqual(["build"]);
    expect(record.setModel).toEqual([{ sessionId: SID, configId: "model", value: "fake-2" }]);
    expect(record.cancelled).toEqual([SID]);
    expect(session.info.currentMode).toBe("build");
    expect(session.info.currentModel).toBe("fake-2");
    await session.close();
  });

  it("replays session/load updates to the first subscriber and lists sessions", async () => {
    const { connection } = await setup();
    const session = await connection.loadSession({ sessionId: SID, cwd: "/w" });
    expect(session.info.currentMode).toBe("plan");
    const events: AgentEvent[] = [];
    session.onEvent((e) => events.push(e));
    expect(events).toEqual([{ type: "user_text", text: "earlier" }]);
    expect(await connection.listSessions("/w")).toEqual([
      { sessionId: SID, cwd: "/w", title: "t", updatedAt: "2026-01-01T00:00:00Z" },
    ]);
    await session.close();
  });

  it("delivers reload history to the replacement session when the id is already registered", async () => {
    const { connection } = await setup();
    const previous = await connection.newSession({ cwd: "/w" });
    const oldEvents: AgentEvent[] = [];
    previous.onEvent((event) => oldEvents.push(event));
    const reloaded = await connection.loadSession({ sessionId: SID, cwd: "/w" });
    const events: AgentEvent[] = [];
    reloaded.onEvent((event) => events.push(event));
    expect(events).toEqual([{ type: "user_text", text: "earlier" }]);
    expect(oldEvents).toEqual([]);
    await reloaded.close();
  });
});
