import { render } from "ink";
import type { SessionHandle, SessionListItem } from "../agent/types.js";
import { App } from "./App.js";

export interface RenderAppOptions {
  listSessions?: () => Promise<SessionListItem[]>;
}

/**
 * TUI를 띄우고 사용자가 종료(/quit, Ctrl+C 두 번)하면 resolve한다.
 * 세션 종료(handle.close)는 호출자 책임이다.
 */
export async function renderApp(handle: SessionHandle, opts: RenderAppOptions = {}): Promise<void> {
  const instance = render(<App handle={handle} listSessions={opts.listSessions} />, {
    // Ctrl+C는 App이 직접 처리한다(진행 중이면 취소, 아니면 두 번 눌러 종료).
    exitOnCtrlC: false,
  });
  await instance.waitUntilExit();
}
