import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "ink-testing-library";
import { App } from "./App.js";
import { FakeSession } from "./test/fake-session.js";
import { KEY, press, tick } from "./test/ink-input.js";

afterEach(cleanup);

async function setup(
  session = new FakeSession(),
  listSessions?: () => Promise<{ sessionId: string; cwd: string }[]>,
  onRunAuth?: (args: string[]) => Promise<number>,
) {
  const r = render(<App handle={session} listSessions={listSessions} onRunAuth={onRunAuth} />);
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
  it("forwards memory controls to the server", async () => {
    const { stdin, session } = await setup();
    await type(stdin, "/memories generate off");
    expect(session.prompts).toEqual(["/memories generate off"]);
    session.finishTurn();
    await tick();
  });
  it("forwards a goal from the slash menu to the server", async () => {
    const { stdin, session } = await setup();
    await type(stdin, "/loop fix tests");
    expect(session.prompts).toEqual(["/loop fix tests"]);
    session.finishTurn();
    await tick();
  });
  it("shows the startup banner until the conversation starts", async () => {
    const { stdin, session, lastFrame } = await setup();
    expect(lastFrame()).toContain("Type a message, or /help for commands.");
    expect(lastFrame()).toContain("/tmp/work");
    await type(stdin, "hello");
    session.emit({ type: "agent_text", text: "Hi" });
    await tick();
    expect(lastFrame()).not.toContain("Type a message, or /help for commands.");
  });

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
    expect(lastFrame()).toContain("도구 실행 중");
    expect(lastFrame()).toContain("(25%)");
    session.emit({ type: "tool_call_update", toolCallId: "t1", status: "completed" });
    session.finishTurn();
    await tick();
    const all = output(frames);
    expect(all).toContain("hello");
    expect(all).toContain("Hi there");
    expect(all).toContain("✓");
    expect(all).toContain("응답 종료");
    expect(all).toContain("최종 답변이 없습니다");
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
    expect(lastFrame()).toContain("승인 대기");
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

  it("handles /agent and /model", async () => {
    const { stdin, session, lastFrame } = await setup();
    await type(stdin, "/agent plan");
    await type(stdin, "/model other-model");
    expect(session.calls).toEqual(["setMode:plan", "setModel:other-model"]);
    expect(lastFrame()).toContain("plan");
    expect(lastFrame()).toContain("other-model");
    await type(stdin, "/agent nope");
    expect(session.calls).toHaveLength(2);
    expect(lastFrame()).toContain("unknown agent: nope");
  });

  it("shows the slash command popup while typing /", async () => {
    const { stdin, session, lastFrame } = await setup();
    await press(stdin, "/");
    expect(lastFrame()).toContain("/agent");
    expect(lastFrame()).toContain("/model");
    expect(lastFrame()).toContain("/sessions");
    expect(session.prompts).toEqual([]);
  });

  it("suggests the session's modes and models as arguments", async () => {
    const { stdin, session, lastFrame } = await setup();
    await press(stdin, ..."/agent ".split(""));
    expect(lastFrame()).toContain("chat");
    expect(lastFrame()).toContain("plan");
    expect(lastFrame()).toContain("build");
    await press(stdin, ...Array(7).fill(KEY.backspace), ..."/model ".split(""));
    expect(lastFrame()).toContain("other-model");
    expect(session.calls).toEqual([]);
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

  it("reports /login as unavailable without onRunAuth", async () => {
    const { stdin, frames } = await setup();
    await type(stdin, "/login");
    await tick();
    expect(output(frames)).toContain("login is not available in this context");
  });

  it("forwards /login flags as separate argv entries", async () => {
    const onRunAuth = vi.fn(async () => 0);
    const { stdin, frames } = await setup(new FakeSession(), undefined, onRunAuth);
    await type(stdin, "/login --provider openai");
    await tick();
    expect(onRunAuth).toHaveBeenCalledWith(["login", "--provider", "openai"]);
    expect(output(frames)).toContain("login succeeded");
  });

  it.each([
    ["openai", "claude-opus-5-5", "gpt-6.1-sol"],
    ["anthropic", "gpt-6.1-sol", "claude-opus-5-5"],
    ["gemini", "gpt-6.1-sol", "gemini-3.8-flash"],
    ["openrouter", "gpt-6.1-sol", "qwen/qwen3-coder-flash"],
  ])("explains model switching after %s login", async (provider, currentModel, target) => {
    const session = new FakeSession({ currentModel, models: [{ value: target, name: target }] });
    const { stdin, frames } = await setup(session, undefined, vi.fn(async () => 0));
    await type(stdin, `/login --provider ${provider}`);
    expect(output(frames)).toContain(`현재 모델: ${currentModel}`);
    expect(output(frames)).toContain(`/model ${target}`);
    expect(session.calls).toEqual([]);
  });

  it("does not suggest a model switch after failed login", async () => {
    const { stdin, frames } = await setup(new FakeSession(), undefined, vi.fn(async () => 2));
    await type(stdin, "/login --provider openai");
    expect(output(frames)).toContain("login failed (exit code 2)");
    expect(output(frames)).not.toContain("현재 모델:");
  });

  it("opens provider tabs and signs in with OpenAI on Enter", async () => {
    const onRunAuth = vi.fn(async () => 0);
    const { stdin, lastFrame, session } = await setup(new FakeSession(), undefined, onRunAuth);
    await type(stdin, "/login");
    expect(lastFrame()).toContain("로그인할 서비스를 선택하세요");
    expect(lastFrame()).toContain("OpenAI");
    expect(lastFrame()).toContain("Claude");
    expect(onRunAuth).not.toHaveBeenCalled();
    await press(stdin, "x");
    expect(lastFrame()).not.toContain("❯ x");
    await press(stdin, "\r");
    expect(onRunAuth).toHaveBeenCalledExactlyOnceWith(["login", "--provider", "openai"]);
    expect(session.prompts).toEqual([]);
  });

  it("selects Claude with Tab and maps it to anthropic", async () => {
    const onRunAuth = vi.fn(async () => 0);
    const { stdin } = await setup(new FakeSession(), undefined, onRunAuth);
    await type(stdin, "/login");
    await press(stdin, KEY.tab, "\r");
    expect(onRunAuth).toHaveBeenCalledExactlyOnceWith(["login", "--provider", "anthropic"]);
  });

  it("cycles provider tabs with arrows and cancels with Escape", async () => {
    const onRunAuth = vi.fn(async () => 0);
    const { stdin, lastFrame, session } = await setup(new FakeSession(), undefined, onRunAuth);
    await type(stdin, "/login");
    await press(stdin, "\u001b[C", "\u001b[D", "\r");
    expect(onRunAuth).toHaveBeenCalledExactlyOnceWith(["login", "--provider", "openai"]);
    await type(stdin, "/login");
    await press(stdin, "\u001b");
    expect(lastFrame()).not.toContain("로그인할 서비스를 선택하세요");
    expect(onRunAuth).toHaveBeenCalledTimes(1);
    await type(stdin, "hello");
    expect(session.prompts).toEqual(["hello"]);
    session.finishTurn();
    await tick();
  });

  it("runs /logout and reports a non-zero exit code as a failure", async () => {
    const onRunAuth = vi.fn(async () => 2);
    const { stdin, frames } = await setup(new FakeSession(), undefined, onRunAuth);
    await type(stdin, "/logout");
    await tick();
    expect(onRunAuth).toHaveBeenCalledWith(["logout"]);
    const all = output(frames);
    expect(all).toContain("logout failed (exit code 2)");
    expect(all).not.toContain("logout succeeded");
  });

  it("refuses /login while a turn is running", async () => {
    const onRunAuth = vi.fn(async () => 0);
    const { stdin, frames } = await setup(new FakeSession(), undefined, onRunAuth);
    await type(stdin, "go");
    await type(stdin, "/login");
    await tick();
    expect(onRunAuth).not.toHaveBeenCalled();
    expect(output(frames)).toContain("before signing in");
  });

  it("shows an initial notice on mount", async () => {
    const session = new FakeSession();
    const { frames } = render(<App handle={session} initialNotice={{ text: "login succeeded", error: false }} />);
    await tick();
    expect(output(frames)).toContain("login succeeded");
  });
});
