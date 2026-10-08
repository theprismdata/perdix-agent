import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "ink-testing-library";
import { VERSION } from "../../version.js";
import { Banner } from "./Banner.js";

afterEach(cleanup);

describe("Banner", () => {
  it("renders cwd, mode, model and a short session id", () => {
    const { lastFrame } = render(
      <Banner cwd="/tmp/work" mode="build" model="fake-model" sessionId="0123456789abcdef" />,
    );
    const frame = lastFrame() ?? "";
    expect(frame).toContain("perdix");
    expect(frame).toContain("/tmp/work");
    expect(frame).toContain("build");
    expect(frame).toContain("fake-model");
    expect(frame).toContain("01234567");
    expect(frame).not.toContain("012345678");
    expect(frame).toContain("/help");
  });

  it("renders the wordmark, version and the real built-in tool groups", () => {
    const { lastFrame } = render(<Banner cwd="/tmp/work" mode="build" model={null} sessionId="abc" />);
    const frame = lastFrame() ?? "";
    expect(frame).toContain("|  _ \\| ____|  _ \\|  _ \\_ _\\ \\/ /");
    expect(frame).toContain(`perdix v${VERSION}`);
    expect(frame).toContain("Tools");
    expect(frame).toMatch(/read:\s+read, glob, grep, ls/);
    expect(frame).toMatch(/write:\s+write, edit/);
    expect(frame).toMatch(/execute:\s+bash/);
    expect(frame).toMatch(/delegate:\s+task/);
  });

  it("falls back to 'default model' when model is null", () => {
    const { lastFrame } = render(<Banner cwd="/tmp/work" mode="chat" model={null} sessionId="abc" />);
    expect(lastFrame()).toContain("default model");
  });
});
