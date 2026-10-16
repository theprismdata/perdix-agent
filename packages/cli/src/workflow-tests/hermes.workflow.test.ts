import { describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { HermesAdapter } from "../hermes/adapter.js";
import type { HermesEvent } from "../hermes/events.js";
import { workflowCommands, workflowFixture } from "./acp-fixture.js";
import { captureWorkflowEvidence } from "./evidence.js";

const sendDispatch = z.object({ type: z.literal("send"), message: z.string(), display: z.string() });

describe("Hermes workflow", () => {
  it.each(workflowCommands)("sends unchanged ACP text when slash.exec dispatches %s", async (command) => {
    // Given
    const fixture = await workflowFixture();
    const adapter = new HermesAdapter(fixture.connection, { cwd: process.cwd() });
    await adapter.request("session.create", {});
    try {
      // When: Hermes의 실제 send dispatch → prompt.submit 경로를 따른다.
      const dispatch = sendDispatch.parse(await adapter.request("slash.exec", {
        session_id: "workflow-session", command: command.slice(1),
      }));
      await adapter.request("prompt.submit", { session_id: "workflow-session", text: dispatch.message });
      // Then
      expect(dispatch.display).toBe(command);
      expect(fixture.received).toEqual([
        { sessionId: "workflow-session", prompt: [{ type: "text", text: command }] },
      ]);
    } finally {
      adapter.disconnect();
      await fixture.connection.close();
    }
  });

  it("offers workflow and its operations when the catalog and completion are requested", async () => {
    // Given
    const fixture = await workflowFixture();
    const adapter = new HermesAdapter(fixture.connection, { cwd: process.cwd() });
    try {
      // When
      const catalog = await adapter.request("commands.catalog", {});
      const completion = await adapter.request("complete.slash", { text: "/work" });
      // Then
      expect(catalog).toMatchObject({
        canon: { "/workflow": "/workflow" },
        sub: { "/workflow": ["plan", "start", "status", "resume", "retry", "accept", "reject"] },
      });
      expect(completion).toMatchObject({ items: [{ text: "workflow", display: "/workflow" }] });
    } finally {
      adapter.disconnect();
      await fixture.connection.close();
    }
  });

  it("emits standard text and plan events when workflow metadata is unknown", async () => {
    // Given
    const fixture = await workflowFixture();
    const adapter = new HermesAdapter(fixture.connection, { cwd: process.cwd() });
    const events: HermesEvent[] = [];
    adapter.onEvent = (event) => events.push(event);
    await adapter.request("session.create", {});
    try {
      // When
      await adapter.request("prompt.submit", { session_id: "workflow-session", text: "/workflow status run-123" });
      // Then
      expect(events).toEqual(expect.arrayContaining([
        expect.objectContaining({ type: "message.delta", payload: { text: "workflow run-123: awaiting_accept" } }),
        expect.objectContaining({ type: "todo.updated", payload: { todos: [{ id: "0", task: "task-456", status: "completed" }], revision: 0 } }),
        expect.objectContaining({ type: "tool.complete" }),
        expect.objectContaining({ type: "message.complete", payload: expect.objectContaining({ status: "complete" }) }),
      ]));
      await captureWorkflowEvidence("task-9-hermes-events.json", {
        events, received: fixture.received,
      });
    } finally {
      adapter.disconnect();
      await fixture.connection.close();
    }
  });

  it("uses session/cancel when an active workflow receives /cancel", async () => {
    // Given
    const fixture = await workflowFixture(true);
    const adapter = new HermesAdapter(fixture.connection, { cwd: process.cwd() });
    await adapter.request("session.create", {});
    const pending = adapter.request("prompt.submit", { session_id: "workflow-session", text: "/workflow start run-123" });
    try {
      await vi.waitFor(() => expect(fixture.received).toHaveLength(1));
      // When
      await adapter.request("slash.exec", { session_id: "workflow-session", command: "cancel" });
      await pending;
      // Then
      expect(fixture.cancelled).toEqual(["workflow-session"]);
    } finally {
      fixture.release();
      adapter.disconnect();
      await fixture.connection.close();
    }
  });
});
