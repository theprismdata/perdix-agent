import { agent, type PromptRequest, type SessionUpdate } from "@perdix/protocol";
import { PerdixConnection } from "../agent/connection.js";

export const workflowCommands = [
  "/workflow plan 두  작업을 병렬로 실행  ",
  "/workflow start run-123",
  "/workflow status run-123",
  "/workflow resume run-123",
  "/workflow retry run-123 task-456",
  "/workflow accept run-123",
  "/workflow reject run-123",
] as const;

export const fallbackUpdates: SessionUpdate[] = [
  {
    sessionUpdate: "agent_message_chunk",
    content: { type: "text", text: "workflow run-123: awaiting_accept" },
    _meta: { perdix: { workflow: { schemaVersion: 999, unknownField: true } } },
  },
  {
    sessionUpdate: "plan",
    entries: [{ content: "task-456", status: "completed", priority: "high" }],
    _meta: { perdix: { workflow: "future-metadata" } },
  },
  {
    sessionUpdate: "tool_call",
    toolCallId: "verify-123",
    title: "workflow verification",
    status: "completed",
    content: [{ type: "content", content: { type: "text", text: "verified: task-456" } }],
    _meta: { perdix: { workflow: { schemaVersion: 999 } } },
  },
];

export async function workflowFixture(hold = false) {
  const received: PromptRequest[] = [];
  const cancelled: string[] = [];
  let release = () => {};
  const pending = new Promise<void>((resolve) => { release = resolve; });
  const app = agent({ name: "workflow-wire-fixture" })
    .onRequest("initialize", (ctx) => ({ protocolVersion: ctx.params.protocolVersion }))
    .onRequest("session/new", () => ({ sessionId: "workflow-session" }))
    .onRequest("session/prompt", async (ctx) => {
      received.push(ctx.params);
      for (const update of fallbackUpdates) {
        await ctx.client.notify("session/update", { sessionId: ctx.params.sessionId, update });
      }
      if (hold) await pending;
      return {
        stopReason: cancelled.length ? "cancelled" : "end_turn",
        _meta: { perdix: { workflow: { schemaVersion: 999, unknownField: true } } },
      };
    })
    .onNotification("session/cancel", (ctx) => {
      cancelled.push(ctx.params.sessionId);
      release();
    });
  const connection = PerdixConnection.open(app);
  await connection.initialize();
  return { connection, received, cancelled, release };
}
