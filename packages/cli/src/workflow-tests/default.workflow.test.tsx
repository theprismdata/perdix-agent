import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "ink-testing-library";
import { App } from "../ui/App.js";
import { workflowCommands, workflowFixture } from "./acp-fixture.js";
import { captureWorkflowEvidence } from "./evidence.js";

afterEach(cleanup);

describe("default TUI workflow", () => {
  it.each(workflowCommands)("sends unchanged ACP text when Enter submits %s", async (command) => {
    // Given: 실제 ACP 연결을 사용하는 Ink 앱.
    const fixture = await workflowFixture();
    const handle = await fixture.connection.newSession({ cwd: process.cwd() });
    const ui = render(<App handle={handle} />);
    try {
      await vi.waitFor(() => expect(ui.stdin.listenerCount("readable")).toBeGreaterThan(0));
      // When: 명령과 Enter를 실제 입력 스트림에 보낸다.
      ui.stdin.write(command);
      await vi.waitFor(() => expect(ui.lastFrame()).toContain(command.trim()));
      ui.stdin.write("\r");
      // Then: 서버 경계에서 인자와 공백을 포함한 원문을 받는다.
      await vi.waitFor(() => expect(fixture.received).toEqual([
        { sessionId: "workflow-session", prompt: [{ type: "text", text: command }] },
      ]));
      await vi.waitFor(() => expect(ui.frames.join("\n")).toContain("awaiting_accept"));
    } finally {
      ui.unmount();
      await fixture.connection.close();
    }
  });

  it("completes the workflow subcommand when Tab selects a partial input", async () => {
    // Given
    const fixture = await workflowFixture();
    const handle = await fixture.connection.newSession({ cwd: process.cwd() });
    const ui = render(<App handle={handle} />);
    try {
      await vi.waitFor(() => expect(ui.stdin.listenerCount("readable")).toBeGreaterThan(0));
      // When
      ui.stdin.write("/work");
      await vi.waitFor(() => expect(ui.lastFrame()).toContain("/workflow"));
      ui.stdin.write("\t");
      await vi.waitFor(() => expect(ui.lastFrame()).toContain("❯ /workflow "));
      ui.stdin.write("ret");
      await vi.waitFor(() => expect(ui.lastFrame()).toContain("retry"));
      ui.stdin.write("\t");
      await vi.waitFor(() => expect(ui.lastFrame()).toContain("❯ /workflow retry"));
      ui.stdin.write(" run-123 task-456");
      await vi.waitFor(() => expect(ui.lastFrame()).toContain("run-123 task-456"));
      ui.stdin.write("\r");
      // Then
      await vi.waitFor(() => expect(fixture.received[0]?.prompt).toEqual([
        { type: "text", text: "/workflow retry run-123 task-456" },
      ]));
    } finally {
      ui.unmount();
      await fixture.connection.close();
    }
  });

  it("keeps standard text, plan and tool output visible and cancels via ACP when metadata is unknown", async () => {
    // Given
    const fixture = await workflowFixture(true);
    const handle = await fixture.connection.newSession({ cwd: process.cwd() });
    const ui = render(<App handle={handle} />);
    try {
      await vi.waitFor(() => expect(ui.stdin.listenerCount("readable")).toBeGreaterThan(0));
      ui.stdin.write("/workflow start run-123");
      await vi.waitFor(() => expect(ui.lastFrame()).toContain("run-123"));
      ui.stdin.write("\r");
      await vi.waitFor(() => {
        const output = ui.frames.join("\n");
        expect(output).toContain("workflow run-123: awaiting_accept");
        expect(output).toContain("task-456");
        expect(output).toContain("workflow verification");
      });
      // When
      ui.stdin.write("\x03");
      // Then
      await vi.waitFor(() => expect(fixture.cancelled).toEqual(["workflow-session"]));
      await captureWorkflowEvidence("task-9-default-tui.json", {
        frames: ui.frames, received: fixture.received, cancelled: fixture.cancelled,
      });
    } finally {
      fixture.release();
      ui.unmount();
      await fixture.connection.close();
    }
  });
});
