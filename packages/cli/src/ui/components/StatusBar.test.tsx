import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "ink-testing-library";
import { formatUsage, StatusBar } from "./StatusBar.js";

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
});
