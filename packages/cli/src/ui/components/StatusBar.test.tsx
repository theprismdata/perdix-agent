import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "ink-testing-library";
import { contextBar, contextBarColor, formatUsage, layoutStatusBar, StatusBar } from "./StatusBar.js";
import { theme } from "../theme.js";

afterEach(cleanup);

describe("StatusBar", () => {
  it("shows mode, model, context percent and short session id", () => {
    const { lastFrame } = render(
      <StatusBar
        mode="build"
        model="claude-x"
        usage={{ used: 50_000, size: 200_000 }}
        sessionId="abcdef0123456789"
        running={false}
      />,
    );
    const frame = lastFrame() ?? "";
    expect(frame).toContain("build");
    expect(frame).toContain("claude-x");
    expect(frame).toContain("ctx 50.0k/200.0k (25%)");
    expect(frame).toContain("abcdef01");
    expect(frame).not.toContain("abcdef012");
  });

  it("shows a spinner only while running", () => {
    const idle = render(<StatusBar mode="m" model={null} usage={null} sessionId="s" running={false} />).lastFrame() ?? "";
    expect(idle).not.toMatch(/[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/);
    cleanup();
    const busy = render(<StatusBar mode="m" model={null} usage={null} sessionId="s" running />).lastFrame() ?? "";
    expect(busy).toMatch(/[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/);
  });

  it("formats usage percent with rounding and empty size", () => {
    expect(formatUsage({ used: 1, size: 3 })).toBe("ctx 1/3 (33%)");
    expect(formatUsage({ used: 10, size: 0 })).toBe("ctx 10/0 (0%)");
    expect(formatUsage(null)).toBe("ctx -");
  });

  it("drops the session id first when the terminal is narrow, keeping mode and model", async () => {
    const props = {
      mode: "build",
      model: "claude-x",
      usage: { used: 50_000, size: 200_000 },
      sessionId: "abcdef0123456789",
      running: false,
    };
    const r = render(<StatusBar {...props} />);
    expect(r.lastFrame() ?? "").toContain("abcdef01");
    // "build · claude-x · ctx 50.0k/200.0k (25%)" = 41칸. 막대와 session id는 들어가지 않는다.
    Object.defineProperty(r.stdout, "columns", { get: () => 45 });
    r.stdout.emit("resize");
    await new Promise((resolve) => setTimeout(resolve, 50));
    const frame = r.lastFrame() ?? "";
    expect(frame).toContain("build");
    expect(frame).toContain("claude-x");
    expect(frame).toContain("ctx 50.0k/200.0k (25%)");
    expect(frame).not.toContain("abcdef01");
    expect(frame).not.toContain("[");
  });

  it("keeps the hint visible even when narrow", async () => {
    const r = render(<StatusBar mode="build" model="m" usage={null} sessionId="abcdef0123456789" running={false} hint="Ctrl+C again to exit" />);
    Object.defineProperty(r.stdout, "columns", { get: () => 30 });
    r.stdout.emit("resize");
    await new Promise((resolve) => setTimeout(resolve, 50));
    const frame = r.lastFrame() ?? "";
    expect(frame).toContain("Ctrl+C again to exit");
    expect(frame).not.toContain("abcdef01");
  });
});

describe("layoutStatusBar", () => {
  const pinned = [{ text: "mode" }, { separator: " · ", text: "model" }];

  it("always includes pinned segments regardless of width", () => {
    expect(layoutStatusBar(pinned, [], 0)).toEqual(pinned);
    expect(layoutStatusBar(pinned, [{ text: "x" }], 3)).toEqual(pinned);
  });

  it("skips a segment that does not fit but still includes a later shorter one", () => {
    const long = { separator: " · ", text: "a-very-long-segment" };
    const short = { separator: " · ", text: "s" };
    // pinned = 12칸, 예산 20 - 12 = 8: long(22)은 빠지고 short(4)는 들어간다.
    expect(layoutStatusBar(pinned, [long, short], 20)).toEqual([...pinned, short]);
  });

  it("includes tail segments in priority order while they fit", () => {
    const a = { text: "aa" };
    const b = { text: "bbb" };
    expect(layoutStatusBar(pinned, [a, b], 17)).toEqual([...pinned, a, b]);
    expect(layoutStatusBar(pinned, [a, b], 16)).toEqual([...pinned, a]);
  });
});

describe("contextBar", () => {
  it("renders a proportionally filled bar", () => {
    expect(contextBar({ used: 50, size: 100 })).toBe(`[${"█".repeat(5)}${"░".repeat(5)}]`);
    expect(contextBar({ used: 200, size: 100 })).toBe(`[${"█".repeat(10)}]`);
    expect(contextBar({ used: 1, size: 4 }, 4)).toBe("[█░░░]");
  });

  it("returns an empty string without usable usage", () => {
    expect(contextBar(null)).toBe("");
    expect(contextBar({ used: 1, size: 0 })).toBe("");
  });
});

describe("contextBarColor", () => {
  it("moves from success to warning to error as the context fills", () => {
    expect(contextBarColor({ used: 59, size: 100 })).toBe(theme.success);
    expect(contextBarColor({ used: 60, size: 100 })).toBe(theme.warning);
    expect(contextBarColor({ used: 85, size: 100 })).toBe(theme.warning);
    expect(contextBarColor({ used: 86, size: 100 })).toBe(theme.error);
  });
});
