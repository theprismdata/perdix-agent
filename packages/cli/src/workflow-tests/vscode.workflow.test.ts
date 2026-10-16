import { describe, expect, it, vi } from "vitest";
import { ChatController } from "../../../vscode/src/chat-controller.js";
import { workflowCommands, workflowFixture } from "./acp-fixture.js";
import { captureWorkflowEvidence } from "./evidence.js";

describe("VS Code workflow compatibility", () => {
  it.each(workflowCommands)("forwards unchanged ACP text when chat sends %s", async (command) => {
    // Given
    const fixture = await workflowFixture();
    const messages: unknown[] = [];
    const controller = new ChatController({
      connect: () => fixture.connection.newSession({ cwd: process.cwd() }),
      post: (message) => messages.push(message),
    });
    try {
      // When
      await controller.send(command);
      // Then
      expect(fixture.received).toEqual([
        { sessionId: "workflow-session", prompt: [{ type: "text", text: command }] },
      ]);
      expect(messages).toEqual(expect.arrayContaining([
        { type: "event", event: { type: "agent_text", text: "workflow run-123: awaiting_accept" } },
        { type: "event", event: { type: "plan", entries: [{ content: "task-456", status: "completed", priority: "high" }] } },
        expect.objectContaining({ type: "event", event: expect.objectContaining({ type: "tool_call", toolCallId: "verify-123" }) }),
      ]));
      if (command === "/workflow status run-123") {
        await captureWorkflowEvidence("task-9-vscode-events.json", {
          messages, received: fixture.received,
        });
      }
    } finally {
      await controller.dispose();
      await fixture.connection.close();
    }
  });

  it("sends session/cancel when chat cancels a running workflow", async () => {
    // Given
    const fixture = await workflowFixture(true);
    const controller = new ChatController({
      connect: () => fixture.connection.newSession({ cwd: process.cwd() }),
      post: () => {},
    });
    const pending = controller.send("/workflow start run-123");
    try {
      await vi.waitFor(() => expect(fixture.received).toHaveLength(1));
      // When
      await controller.cancel();
      await pending;
      // Then
      expect(fixture.cancelled).toEqual(["workflow-session"]);
      expect(controller.isBusy).toBe(false);
    } finally {
      fixture.release();
      await controller.dispose();
      await fixture.connection.close();
    }
  });
});
