import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "ink-testing-library";
import { App } from "./App.js";
import { FakeSession } from "./test/fake-session.js";
import { KEY, press, tick } from "./test/ink-input.js";

afterEach(cleanup);

async function setup(session = new FakeSession(), listSessions?: () => Promise<{ sessionId: string; cwd: string }[]>) {
  const r = render(<App handle={session} listSessions={listSessions} />);
  await tick();
  return { ...r, session };
}

async function type(stdin: { write: (d: string) => void }, text: string) {
  await press(stdin, ...text.split(""), KEY.enter);
}

/** Static 출력은 앞 프레임에 남으므로 전체 출력에서 찾는다. */
function output(frames: string[]): string {
  return frames.join("\n");
}

describe("App", () => {
  it("sends a prompt and renders streamed events", async () => {
    const { stdin, session, lastFrame, frames } = await setup();
    await type(stdin, "hello");
    expect(session.prompts).toEqual(["hello"]);
    session.emit({ type: "agent_text", text: "Hi " });
    session.emit({ type: "agent_text", text: "there" });
    session.emit({ type: "tool_call", toolCallId: "t1", title: "Read a.py", kind: "read", status: "in_progress" });
    session.emit({ type: "usage", used: 1000, size: 4000 });
    await tick();
    expect(lastFrame()).toContain("Read a.py");
    expect(lastFrame()).toContain("(25%)");
    session.emit({ type: "tool_call_update", toolCallId: "t1", status: "completed" });
    session.finishTurn();
    await tick();
    const all = output(frames);
    expect(all).toContain("hello");
    expect(all).toContain("Hi there");
    expect(all).toContain("✓");
  });

  it("routes permission requests to the prompt and disables input", async () => {
    const { stdin, session, lastFrame } = await setup();
    await type(stdin, "go");
    const answer = session.requestPermission({
      requestId: "r1",
      toolCallId: "t1",
      title: "Write file",
      options: [
        { optionId: "ok", name: "Allow", kind: "allow_once" },
        { optionId: "no", name: "Reject", kind: "reject_once" },
      ],
    });
    await tick();
    expect(lastFrame()).toContain("Permission required");
    await press(stdin, "n");
    await expect(answer).resolves.toEqual({ optionId: "no" });
    expect(lastFrame()).not.toContain("Permission required");
    // 'n'은 입력창으로 새지 않는다
    expect(lastFrame()).not.toContain("❯ n");
  });

  it("Ctrl+C cancels a running turn and resolves pending permissions as cancelled", async () => {
    const { stdin, session } = await setup();
    await type(stdin, "go");
    const answer = session.requestPermission({
      requestId: "r1",
      toolCallId: "t1",
      title: "x",
      options: [{ optionId: "ok", name: "Allow", kind: "allow_once" }],
    });
    await tick();
    await press(stdin, KEY.ctrlC);
    expect(session.calls).toContain("cancel");
    await expect(answer).resolves.toEqual({ cancelled: true });
  });

  it("Ctrl+C twice when idle exits", async () => {
    const { stdin, session, lastFrame } = await setup();
    await press(stdin, KEY.ctrlC);
    expect(lastFrame()).toContain("Press Ctrl+C again to exit");
    expect(session.calls).not.toContain("cancel");
    // 종료(언마운트)되면 이후 입력은 렌더되지 않는다
    await press(stdin, KEY.ctrlC, "z");
    expect(lastFrame()).not.toContain("❯ z");
  });

  it("handles /mode and /model", async () => {
    const { stdin, session, lastFrame } = await setup();
    await type(stdin, "/mode plan");
    await type(stdin, "/model other-model");
    expect(session.calls).toEqual(["setMode:plan", "setModel:other-model"]);
    expect(lastFrame()).toContain("plan");
    expect(lastFrame()).toContain("other-model");
    await type(stdin, "/mode nope");
    expect(session.calls).toHaveLength(2);
    expect(lastFrame()).toContain("unknown mode: nope");
  });

  it("lists sessions and shows help", async () => {
    const { stdin, frames } = await setup(new FakeSession(), async () => [
      { sessionId: "deadbeefcafebabe", cwd: "/w" },
    ]);
    await type(stdin, "/sessions");
    await tick();
    await type(stdin, "/help");
    const all = output(frames);
    expect(all).toContain("deadbeef");
    expect(all).toContain("perdix --resume <id>");
    expect(all).toContain("/quit");
  });

  it("rejects a second prompt while a turn is running", async () => {
    const { stdin, session, frames } = await setup();
    await type(stdin, "one");
    await type(stdin, "two");
    expect(session.prompts).toEqual(["one"]);
    session.finishTurn();
    await tick();
    expect(output(frames)).toContain("a turn is already running");
  });
});
