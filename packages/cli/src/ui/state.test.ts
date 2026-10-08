import { describe, expect, it } from "vitest";
import type { AgentEvent } from "../agent/types.js";
import { initialState, reduce, reduceUi, type State, type ToolItem } from "./state.js";

const base = initialState({ sessionId: "s1", currentMode: "build", currentModel: "m" });

function run(events: AgentEvent[], from: State = base): State {
  return events.reduce(reduce, from);
}

describe("reduce", () => {
  it("accumulates streamed agent text into one live item", () => {
    const s = run([
      { type: "turn_started" },
      { type: "agent_text", text: "Hel" },
      { type: "agent_text", text: "lo " },
      { type: "agent_text", text: "world" },
    ]);
    expect(s.running).toBe(true);
    expect(s.done).toEqual([]);
    expect(s.live).toHaveLength(1);
    expect(s.live[0]).toMatchObject({ kind: "agent", text: "Hello world" });
  });

  it("keeps thoughts and text as separate items and settles finished text", () => {
    const s = run([
      { type: "turn_started" },
      { type: "agent_thought", text: "think" },
      { type: "agent_thought", text: "ing" },
      { type: "agent_text", text: "answer" },
    ]);
    expect(s.done).toEqual([expect.objectContaining({ kind: "thought", text: "thinking" })]);
    expect(s.live).toEqual([expect.objectContaining({ kind: "agent", text: "answer" })]);
  });

  it("tracks tool_call then tool_call_update completed", () => {
    let s = run([
      { type: "turn_started" },
      { type: "agent_text", text: "reading" },
      { type: "tool_call", toolCallId: "t1", title: "Read a.py", kind: "read", status: "pending" },
    ]);
    // 텍스트는 뒤에 도구가 왔으므로 정착
    expect(s.done.map((x) => x.kind)).toEqual(["agent"]);
    expect(s.live).toHaveLength(1);
    s = reduce(s, { type: "tool_call_update", toolCallId: "t1", status: "in_progress" });
    expect((s.live[0] as ToolItem).status).toBe("in_progress");
    s = reduce(s, {
      type: "tool_call_update",
      toolCallId: "t1",
      status: "completed",
      content: [{ type: "text", text: "print(1)" }],
    });
    const tool = s.live[0] as ToolItem;
    expect(tool.status).toBe("completed");
    expect(tool.content).toEqual([{ type: "text", text: "print(1)" }]);
    // 가장 최근 도구는 턴 끝까지 live에 남는다
    expect(s.live).toHaveLength(1);
    s = reduce(s, { type: "turn_ended", stopReason: "end_turn" });
    expect(s.running).toBe(false);
    expect(s.live).toEqual([]);
    const frozen = s.done[1] as ToolItem;
    expect(frozen).toMatchObject({ kind: "tool", status: "completed", expanded: true });
  });

  it("marks failed tools and collapses earlier completed tools", () => {
    const s = run([
      { type: "turn_started" },
      { type: "tool_call", toolCallId: "a", title: "ls", kind: "execute", status: "in_progress" },
      { type: "tool_call_update", toolCallId: "a", status: "completed" },
      { type: "tool_call", toolCallId: "b", title: "rm", kind: "execute", status: "in_progress" },
      { type: "tool_call_update", toolCallId: "b", status: "failed", content: [{ type: "text", text: "boom" }] },
    ]);
    // a는 뒤에 b가 와서 접힌 채 정착, b는 최근 도구라 live에 남는다
    expect(s.done).toEqual([expect.objectContaining({ toolCallId: "a", expanded: false })]);
    expect(s.live).toEqual([expect.objectContaining({ toolCallId: "b", status: "failed" })]);
  });

  it("does not settle an unfinished tool even when later items exist", () => {
    const s = run([
      { type: "turn_started" },
      { type: "tool_call", toolCallId: "a", title: "slow", kind: "execute", status: "in_progress" },
      { type: "tool_call", toolCallId: "b", title: "fast", kind: "read", status: "completed" },
      { type: "agent_text", text: "x" },
    ]);
    expect(s.done).toEqual([]);
    expect(s.live.map((x) => x.id)).toHaveLength(3);
  });

  it("computes nesting depth from parentToolCallId", () => {
    const s = run([
      { type: "turn_started" },
      { type: "tool_call", toolCallId: "p", title: "task", kind: "other", status: "in_progress" },
      { type: "tool_call", toolCallId: "c", title: "child", kind: "read", status: "pending", parentToolCallId: "p" },
      { type: "tool_call", toolCallId: "g", title: "grand", kind: "read", status: "pending", parentToolCallId: "c" },
    ]);
    const [p, c, g] = s.live as ToolItem[];
    expect(p!.depth).toBe(0);
    expect(c).toMatchObject({ depth: 1, parentToolCallId: "p" });
    expect(g!.depth).toBe(2);
  });

  it("treats a repeated tool_call as an upsert", () => {
    const s = run([
      { type: "tool_call", toolCallId: "t", title: "old", kind: "read", status: "pending" },
      { type: "tool_call", toolCallId: "t", title: "new", kind: "read", status: "in_progress" },
    ]);
    expect(s.live).toHaveLength(1);
    expect(s.live[0]).toMatchObject({ title: "new", status: "in_progress" });
  });

  it("ignores updates for unknown tool ids", () => {
    const s = reduce(base, { type: "tool_call_update", toolCallId: "nope", status: "completed" });
    expect(s).toBe(base);
  });

  it("handles turn_started/turn_ended and notes non end_turn stop reasons", () => {
    let s = run([{ type: "turn_started" }, { type: "agent_text", text: "partial" }]);
    expect(s.running).toBe(true);
    s = reduce(s, { type: "turn_ended", stopReason: "cancelled", verification: "failed" });
    expect(s.running).toBe(false);
    expect(s.live).toEqual([]);
    expect(s.done.map((x) => x.kind)).toEqual(["agent", "notice"]);
    expect(s.done[1]).toMatchObject({ text: "turn ended: cancelled · verification: failed" });
  });

  it("does not add a notice for a plain end_turn", () => {
    const s = run([{ type: "turn_started" }, { type: "agent_text", text: "ok" }, { type: "turn_ended", stopReason: "end_turn" }]);
    expect(s.done.map((x) => x.kind)).toEqual(["agent"]);
  });

  it("updates usage, mode and model", () => {
    const s = run([
      { type: "usage", used: 500, size: 1000 },
      { type: "mode_changed", modeId: "plan" },
      { type: "model_changed", model: "x" },
    ]);
    expect(s.usage).toEqual({ used: 500, size: 1000, cost: null });
    expect(s.mode).toBe("plan");
    expect(s.model).toBe("x");
  });

  it("replaces a live plan in place", () => {
    const s = run([
      { type: "plan", entries: [{ content: "a", status: "pending" }] },
      { type: "plan", entries: [{ content: "a", status: "completed" }] },
    ]);
    expect(s.live).toHaveLength(1);
    expect(s.live[0]).toMatchObject({ kind: "plan", entries: [{ content: "a", status: "completed" }] });
  });

  it("stops running on server_exit", () => {
    const s = run([{ type: "turn_started" }, { type: "server_exit", code: 1 }]);
    expect(s.running).toBe(false);
    expect(s.serverExited).toBe(true);
    expect(s.done[0]).toMatchObject({ kind: "error", text: "server exited (code 1)" });
  });

  it("assigns unique ids", () => {
    const s = run([
      { type: "user_text", text: "q" },
      { type: "agent_text", text: "a" },
      { type: "error", message: "e" },
    ]);
    const ids = [...s.done, ...s.live].map((x) => x.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("reduceUi", () => {
  it("ui_idle settles everything only while running", () => {
    const running = run([{ type: "turn_started" }, { type: "agent_text", text: "x" }]);
    const idle = reduceUi(running, { type: "ui_idle" });
    expect(idle.running).toBe(false);
    expect(idle.live).toEqual([]);
    expect(reduceUi(idle, { type: "ui_idle" })).toBe(idle);
  });

  it("ui_notice adds a notice item", () => {
    const s = reduceUi(base, { type: "ui_notice", text: "hi" });
    expect(s.done).toEqual([expect.objectContaining({ kind: "notice", text: "hi" })]);
  });
});
