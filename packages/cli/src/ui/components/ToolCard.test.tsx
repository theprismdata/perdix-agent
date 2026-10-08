import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "ink-testing-library";
import type { ToolItem } from "../state.js";
import { diffLines, summarize, ToolCard } from "./ToolCard.js";

afterEach(cleanup);

function tool(over: Partial<ToolItem> = {}): ToolItem {
  return {
    kind: "tool",
    id: "i0",
    toolCallId: "t1",
    title: "Read src/a.py",
    toolKind: "read",
    status: "pending",
    parentToolCallId: null,
    depth: 0,
    content: [],
    expanded: false,
    ...over,
  };
}

describe("ToolCard", () => {
  it.each([
    ["pending", "○"],
    ["in_progress", "◐"],
    ["completed", "✓"],
    ["failed", "✗"],
  ] as const)("shows %s status icon", (status, icon) => {
    const { lastFrame } = render(<ToolCard item={tool({ status })} expanded={false} />);
    const frame = lastFrame() ?? "";
    expect(frame).toContain(icon);
    expect(frame).toContain("read");
    expect(frame).toContain("Read src/a.py");
  });

  it("collapsed card shows a summary instead of content", () => {
    const item = tool({ status: "completed", content: [{ type: "text", text: "a\nb\nc" }] });
    const frame = render(<ToolCard item={item} expanded={false} />).lastFrame() ?? "";
    expect(frame).toContain("(3 lines)");
    expect(frame).not.toContain("\nb");
  });

  it("truncates text content to 12 lines", () => {
    const text = Array.from({ length: 20 }, (_, i) => `line${i + 1}`).join("\n");
    const item = tool({ status: "completed", content: [{ type: "text", text }] });
    const frame = render(<ToolCard item={item} expanded />).lastFrame() ?? "";
    expect(frame).toContain("line12");
    expect(frame).not.toContain("line13");
    expect(frame).toContain("… 8 lines");
  });

  it("renders a colored diff limited to 40 lines", () => {
    const newText = Array.from({ length: 50 }, (_, i) => `n${i}`).join("\n");
    const item = tool({
      toolKind: "edit",
      status: "completed",
      content: [{ type: "diff", path: "a.txt", oldText: null, newText }],
    });
    const frame = render(<ToolCard item={item} expanded />).lastFrame() ?? "";
    expect(frame).toContain("a.txt");
    expect(frame).toContain("+ n39");
    expect(frame).not.toContain("+ n40");
    expect(frame).toContain("… 10 lines");
  });

  it("renders removed and added lines of an edit diff", () => {
    const item = tool({
      toolKind: "edit",
      status: "completed",
      content: [{ type: "diff", path: "b.txt", oldText: "a\nold\nz\n", newText: "a\nnew\nz\n" }],
    });
    const frame = render(<ToolCard item={item} expanded />).lastFrame() ?? "";
    expect(frame).toContain("- old");
    expect(frame).toContain("+ new");
    expect(frame).toContain("  a");
  });

  it("indents nested cards", () => {
    const top = render(<ToolCard item={tool()} expanded={false} />).lastFrame() ?? "";
    cleanup();
    const nested = render(<ToolCard item={tool({ depth: 2, parentToolCallId: "p" })} expanded={false} />).lastFrame() ?? "";
    expect(top.startsWith("○")).toBe(true);
    expect(nested.startsWith("    ○")).toBe(true);
  });
});

describe("diffLines", () => {
  it("trims common prefix/suffix with one context line each side", () => {
    expect(diffLines("a\nb\nc\nd\n", "a\nb\nX\nd\n")).toEqual([
      { sign: " ", text: "b" },
      { sign: "-", text: "c" },
      { sign: "+", text: "X" },
      { sign: " ", text: "d" },
    ]);
  });

  it("summarizes added/removed counts", () => {
    expect(summarize([{ type: "diff", path: "p", oldText: "a\nb", newText: "a\nc\nd" }])).toBe("+2 -1");
  });
});
