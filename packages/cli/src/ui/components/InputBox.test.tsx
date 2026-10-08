import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "ink-testing-library";
import { KEY, press, tick } from "../test/ink-input.js";
import { InputBox, type SlashCommandHint } from "./InputBox.js";

afterEach(cleanup);

const COMMANDS: SlashCommandHint[] = [
  { name: "/mode", hint: "switch mode" },
  { name: "/model", hint: "switch model" },
  { name: "/help", hint: "show help" },
];

const COMMANDS_WITH_ARGS: SlashCommandHint[] = [
  { name: "/mode", hint: "switch mode", args: ["chat", "plan", "build"] },
  { name: "/model", hint: "switch model", args: ["claude-opus-5-5", "claude-sonnet-5-5"] },
  { name: "/help", hint: "show help" },
];

async function setup(active = true, commands: SlashCommandHint[] = []) {
  const onSubmit = vi.fn();
  const r = render(<InputBox active={active} onSubmit={onSubmit} commands={commands} />);
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

  describe("slash command popup", () => {
    it("shows every command when only / is typed", async () => {
      const { stdin, lastFrame } = await setup(true, COMMANDS);
      await press(stdin, "/");
      expect(lastFrame()).toContain("/mode");
      expect(lastFrame()).toContain("/model");
      expect(lastFrame()).toContain("/help");
    });

    it("narrows suggestions by prefix", async () => {
      const { stdin, lastFrame } = await setup(true, COMMANDS);
      await press(stdin, "/", "m", "o");
      expect(lastFrame()).toContain("/mode");
      expect(lastFrame()).toContain("/model");
      expect(lastFrame()).not.toContain("/help");
    });

    it("moves the highlight with down and completes it with Tab", async () => {
      const { stdin, lastFrame, onSubmit } = await setup(true, COMMANDS);
      await press(stdin, "/", "m", "o", KEY.down, KEY.tab);
      expect(lastFrame()).toContain("❯ /model ");
      expect(lastFrame()).not.toContain("switch mode");
      expect(onSubmit).not.toHaveBeenCalled();
    });

    it("completes the top match with Tab", async () => {
      const { stdin, lastFrame } = await setup(true, COMMANDS);
      await press(stdin, "/", "m", "o", KEY.tab);
      expect(lastFrame()).toContain("❯ /mode ");
      expect(lastFrame()).not.toContain("/model");
    });

    it("completes a partial command on Enter instead of submitting", async () => {
      const { stdin, lastFrame, onSubmit } = await setup(true, COMMANDS);
      await press(stdin, "/", "m", "o", KEY.enter);
      expect(onSubmit).not.toHaveBeenCalled();
      expect(lastFrame()).toContain("❯ /mode ");
    });

    it("submits an exact command on Enter even if it prefixes another", async () => {
      const { stdin, onSubmit } = await setup(true, COMMANDS);
      await press(stdin, "/", "m", "o", "d", "e", KEY.enter);
      expect(onSubmit).toHaveBeenCalledWith("/mode");
    });

    it("shows no popup and keeps history navigation without commands", async () => {
      const { stdin, lastFrame } = await setup();
      await press(stdin, "/", "a", KEY.enter, "/");
      expect(lastFrame()).not.toContain("/a");
      await press(stdin, KEY.up);
      expect(lastFrame()).toContain("/a");
    });
  });

  describe("argument popup", () => {
    it("shows every argument after the command and a space", async () => {
      const { stdin, lastFrame } = await setup(true, COMMANDS_WITH_ARGS);
      await press(stdin, "/", "m", "o", "d", "e", " ");
      expect(lastFrame()).toContain("chat");
      expect(lastFrame()).toContain("plan");
      expect(lastFrame()).toContain("build");
      expect(lastFrame()).not.toContain("claude-opus-5-5");
    });

    it("narrows arguments by prefix", async () => {
      const { stdin, lastFrame } = await setup(true, COMMANDS_WITH_ARGS);
      await press(stdin, "/", "m", "o", "d", "e", " ", "b");
      expect(lastFrame()).toContain("build");
      expect(lastFrame()).not.toContain("chat");
      expect(lastFrame()).not.toContain("plan");
    });

    it("completes an argument with Tab without a trailing space", async () => {
      const { stdin, lastFrame, onSubmit } = await setup(true, COMMANDS_WITH_ARGS);
      await press(stdin, "/", "m", "o", "d", "e", " ", "b", KEY.tab);
      expect(lastFrame()).toContain("❯ /mode build");
      expect(onSubmit).not.toHaveBeenCalled();
      await press(stdin, KEY.enter);
      expect(onSubmit).toHaveBeenCalledWith("/mode build");
    });

    it("completes a partial argument on Enter instead of submitting", async () => {
      const { stdin, lastFrame, onSubmit } = await setup(true, COMMANDS_WITH_ARGS);
      await press(stdin, "/", "m", "o", "d", "e", " ", "b", KEY.enter);
      expect(onSubmit).not.toHaveBeenCalled();
      expect(lastFrame()).toContain("❯ /mode build");
    });

    it("submits an exactly typed argument on Enter", async () => {
      const { stdin, onSubmit } = await setup(true, COMMANDS_WITH_ARGS);
      await press(stdin, ..."/mode build".split(""), KEY.enter);
      expect(onSubmit).toHaveBeenCalledWith("/mode build");
    });

    it("shows no argument popup for a command without args", async () => {
      const { stdin, lastFrame, onSubmit } = await setup(true, COMMANDS_WITH_ARGS);
      await press(stdin, "/", "h", "e", "l", "p", " ");
      expect(lastFrame()).not.toContain("show help");
      await press(stdin, "x", KEY.enter);
      expect(onSubmit).toHaveBeenCalledWith("/help x");
    });
  });
});
