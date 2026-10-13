import { render, type Instance } from "ink";
import { runAuthCommand } from "../agent/auth.js";
import type { SessionHandle, SessionListItem } from "../agent/types.js";
import { App, authResultNotice } from "./App.js";

export interface RenderAppOptions {
  listSessions?: () => Promise<SessionListItem[]>;
  /** 테스트용: 실제 `runAuthCommand` 대신 주입. */
  runAuth?: (args: string[]) => Promise<number>;
}

/**
 * TUI를 띄우고 사용자가 종료(/quit, Ctrl+C 두 번)하면 resolve한다.
 * 세션 종료(handle.close)는 호출자 책임이다.
 *
 * `/login`, `/logout`은 터미널을 직접 써야 하므로(ant 자식 프로세스, raw 모드 키 입력)
 * Ink 인스턴스를 내리고 인증 명령을 실행한 뒤 같은 handle로 App을 다시 띄운다.
 * 이 재마운트 중의 언마운트는 사용자 종료가 아니므로 바깥 promise를 resolve하지 않는다.
 */
export function renderApp(handle: SessionHandle, opts: RenderAppOptions = {}): Promise<void> {
  const runAuth = opts.runAuth ?? ((args: string[]) => runAuthCommand(args));
  return new Promise<void>((resolve, reject) => {
    let instance: Instance;
    let remounting = false;

    const runAuthInline = async (args: string[]): Promise<number> => {
      remounting = true;
      let code = 1;
      let notice = authResultNotice(args[0] ?? "auth", code);
      try {
        instance.unmount();
        // Ink 정리(raw 모드 해제, 커서 복원)가 끝난 뒤 터미널을 넘긴다.
        await instance.waitUntilExit();
        // unmount는 이미 그린 프레임(입력창·상태바)을 지우지 않는다. 지우지 않으면
        // 인증 명령 출력 위에 옛 화면이 그대로 남고, 재마운트된 새 화면이 그 아래
        // 이어 그려져 화면이 두 겹으로 겹친다. /clear와 같은 시퀀스로 지운다.
        process.stdout.write("\x1b[2J\x1b[3J\x1b[H");
        code = await runAuth(args);
        notice = authResultNotice(args[0] ?? "auth", code, { args, info: handle.info });
      } catch (err) {
        // runAuth 자체가 예외를 던지면(코드가 아니라 진짜 실패) 그 메시지를 보여준다.
        // 여기서 밖으로 다시 던지지 않는다 — 호출한 쪽(이미 언마운트된 옛 App)의
        // onRunAuth 호출이 reject되면 죽은 컴포넌트에서 쓸모없는 알림이 한 번 더 돈다.
        const message = err instanceof Error ? err.message : String(err);
        notice = { text: `${args[0] ?? "auth"} failed: ${message}`, error: true };
      } finally {
        // 호출한 App은 이미 언마운트되어 그 알림은 화면에 닿지 않으므로 새 App이 결과를 띄운다.
        mount(notice);
        remounting = false;
      }
      return code;
    };

    const mount = (initialNotice?: { text: string; error: boolean }): void => {
      const current = render(
        <App
          handle={handle}
          listSessions={opts.listSessions}
          onRunAuth={runAuthInline}
          initialNotice={initialNotice}
        />,
        // Ctrl+C는 App이 직접 처리한다(진행 중이면 취소, 아니면 두 번 눌러 종료).
        { exitOnCtrlC: false },
      );
      instance = current;
      current.waitUntilExit().then(
        () => {
          if (!remounting) resolve();
        },
        (err: unknown) => {
          if (!remounting) reject(err);
        },
      );
    };

    mount();
  });
}
