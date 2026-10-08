import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "ink-testing-library";
import type { PermissionRequest } from "../../agent/types.js";
import { KEY, press, tick } from "../test/ink-input.js";
import { PermissionPrompt } from "./PermissionPrompt.js";

afterEach(cleanup);

const request: PermissionRequest = {
  requestId: "r1",
  toolCallId: "t1",
  title: "Run `rm -rf build`",
  kind: "execute",
  options: [
    { optionId: "opt-allow", name: "Allow once", kind: "allow_once" },
    { optionId: "opt-always", name: "Always allow", kind: "allow_always" },
    { optionId: "opt-reject", name: "Reject", kind: "reject_once" },
    { optionId: "opt-never", name: "Always reject", kind: "reject_always" },
  ],
};

async function setup() {
  const onAnswer = vi.fn();
  const r = render(<PermissionPrompt request={request} onAnswer={onAnswer} />);
  await tick();
  return { ...r, onAnswer };
}

describe("PermissionPrompt", () => {
  it("renders title and options", async () => {
    const { lastFrame } = await setup();
    const frame = lastFrame() ?? "";
    expect(frame).toContain("Run `rm -rf build`");
    expect(frame).toContain("Allow once (y)");
    expect(frame).toContain("Always reject (N)");
  });

  it.each([
    ["y", "opt-allow"],
    ["a", "opt-always"],
    ["n", "opt-reject"],
    ["N", "opt-never"],
    ["3", "opt-reject"],
  ])("key %s selects %s", async (key, optionId) => {
    const { stdin, onAnswer } = await setup();
    await press(stdin, key);
    expect(onAnswer).toHaveBeenCalledTimes(1);
    expect(onAnswer).toHaveBeenCalledWith({ optionId });
  });

  it("arrow keys + Enter select the highlighted option", async () => {
    const { stdin, onAnswer, lastFrame } = await setup();
    await press(stdin, KEY.down, KEY.down);
    expect(lastFrame()).toContain("❯ 3. Reject");
    await press(stdin, KEY.up, KEY.enter);
    expect(onAnswer).toHaveBeenCalledWith({ optionId: "opt-always" });
  });

  it("up from the first option wraps to the last", async () => {
    const { stdin, onAnswer } = await setup();
    await press(stdin, KEY.up, KEY.enter);
    expect(onAnswer).toHaveBeenCalledWith({ optionId: "opt-never" });
  });

  it("Escape answers reject_once", async () => {
    const { stdin, onAnswer } = await setup();
    await press(stdin, KEY.escape);
    expect(onAnswer).toHaveBeenCalledWith({ optionId: "opt-reject" });
  });

  it("ignores unrelated keys", async () => {
    const { stdin, onAnswer } = await setup();
    await press(stdin, "x", "9");
    expect(onAnswer).not.toHaveBeenCalled();
  });
});
