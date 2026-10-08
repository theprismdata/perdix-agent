import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "ink-testing-library";
import { KEY, press, tick } from "../test/ink-input.js";
import { InputBox } from "./InputBox.js";

afterEach(cleanup);

async function setup(active = true) {
  const onSubmit = vi.fn();
  const r = render(<InputBox active={active} onSubmit={onSubmit} />);
  await tick();
  return { ...r, onSubmit };
}

describe("InputBox", () => {
  it("submits typed text on Enter and clears", async () => {
    const { stdin, onSubmit, lastFrame } = await setup();
    await press(stdin, "h", "i");
    expect(lastFrame()).toContain("hi");
    await press(stdin, KEY.enter);
    expect(onSubmit).toHaveBeenCalledWith("hi");
    expect(lastFrame()).not.toContain("hi");
  });

  it("continues the line when it ends with a backslash", async () => {
    const { stdin, onSubmit } = await setup();
    await press(stdin, "a", "\\", KEY.enter, "b", KEY.enter);
    expect(onSubmit).toHaveBeenCalledWith("a\nb");
  });

  it("does not submit blank input and supports backspace", async () => {
    const { stdin, onSubmit } = await setup();
    await press(stdin, " ", KEY.enter);
    expect(onSubmit).not.toHaveBeenCalled();
    await press(stdin, KEY.backspace, "x", "y", KEY.backspace, KEY.enter);
    expect(onSubmit).toHaveBeenCalledWith("x");
  });

  it("navigates history with up/down", async () => {
    const { stdin, onSubmit, lastFrame } = await setup();
    await press(stdin, "o", "n", "e", KEY.enter, "t", "w", "o", KEY.enter, "d", "r");
    await press(stdin, KEY.up);
    expect(lastFrame()).toContain("two");
    await press(stdin, KEY.up);
    expect(lastFrame()).toContain("one");
    await press(stdin, KEY.down, KEY.down);
    expect(lastFrame()).toContain("dr");
    await press(stdin, KEY.up, KEY.up, KEY.enter);
    expect(onSubmit).toHaveBeenLastCalledWith("one");
  });

  it("ignores input when inactive", async () => {
    const { stdin, onSubmit } = await setup(false);
    await press(stdin, "x", KEY.enter);
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
