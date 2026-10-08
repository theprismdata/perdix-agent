import { describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import type { AppProps } from "./App.js";
import { FakeSession } from "./test/fake-session.js";
import { tick } from "./test/ink-input.js";

interface FakeInstance {
  props: AppProps;
  unmounted: boolean;
  unmount: () => void;
  waitUntilExit: () => Promise<void>;
}

const mounts: FakeInstance[] = [];

// 실제 터미널 대신 마운트/언마운트만 기록하는 가짜 Ink render.
vi.mock("ink", async (importOriginal) => {
  const actual = await importOriginal<typeof import("ink")>();
  return {
    ...actual,
    render: (el: ReactElement<AppProps>) => {
      let done!: () => void;
      const exited = new Promise<void>((r) => (done = r));
      const inst: FakeInstance = {
        props: el.props,
        unmounted: false,
        unmount: () => {
          inst.unmounted = true;
          done();
        },
        waitUntilExit: () => exited,
      };
      mounts.push(inst);
      return inst;
    },
  };
});

const { renderApp } = await import("./index.js");

describe("renderApp", () => {
  it("unmounts, runs auth, remounts, and resolves only on a real exit", async () => {
    mounts.length = 0;
    const session = new FakeSession();
    const runAuth = vi.fn(async (args: string[]) => {
      // 인증 실행 중에는 이전 인스턴스가 내려가 있어야 한다.
      expect(mounts.every((m) => m.unmounted)).toBe(true);
      return args[0] === "login" ? 0 : 3;
    });
    let resolved = false;
    const done = renderApp(session, { runAuth }).then(() => {
      resolved = true;
    });

    const onRunAuth = mounts[0]?.props.onRunAuth;
    expect(onRunAuth).toBeDefined();
    await expect(onRunAuth?.(["login", "--provider", "openai"])).resolves.toBe(0);
    expect(runAuth).toHaveBeenCalledWith(["login", "--provider", "openai"]);
    await tick();
    expect(resolved).toBe(false);
    expect(mounts).toHaveLength(2);
    expect(mounts[1]?.props.handle).toBe(session);
    expect(mounts[1]?.props.initialNotice).toEqual({ text: "login succeeded", error: false });

    await expect(mounts[1]?.props.onRunAuth?.(["logout"])).resolves.toBe(3);
    expect(mounts).toHaveLength(3);
    expect(mounts[2]?.props.initialNotice).toEqual({ text: "logout failed (exit code 3)", error: true });
    await tick();
    expect(resolved).toBe(false);

    mounts[2]?.unmount();
    await done;
    expect(resolved).toBe(true);
  });

  it("surfaces the real error and still remounts when runAuth throws", async () => {
    mounts.length = 0;
    const session = new FakeSession();
    const runAuth = vi.fn(async () => {
      throw new Error("ant not found");
    });
    let resolved = false;
    const done = renderApp(session, { runAuth }).then(() => {
      resolved = true;
    });

    const onRunAuth = mounts[0]?.props.onRunAuth;
    // onRunAuth never rejects even though runAuth threw — the caller (an
    // already-unmounted App) must not hit a dead-component .catch() path.
    await expect(onRunAuth?.(["login"])).resolves.toBe(1);
    await tick();
    expect(mounts).toHaveLength(2);
    expect(mounts[1]?.props.initialNotice).toEqual({ text: "login failed: ant not found", error: true });
    expect(resolved).toBe(false);

    mounts[1]?.unmount();
    await done;
    expect(resolved).toBe(true);
  });
});
