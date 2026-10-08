import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { Box, useApp, useInput, useStdout } from "ink";
import type { PermissionAnswer, PermissionRequest, SessionHandle, SessionListItem } from "../agent/types.js";
import { initialState, reduceUi } from "./state.js";
import { Transcript } from "./components/Transcript.js";
import { PermissionPrompt } from "./components/PermissionPrompt.js";
import { InputBox } from "./components/InputBox.js";
import { StatusBar } from "./components/StatusBar.js";

export interface AppProps {
  handle: SessionHandle;
  listSessions?: () => Promise<SessionListItem[]>;
}

interface PendingPermission {
  request: PermissionRequest;
  resolve: (answer: PermissionAnswer) => void;
}

/** Ctrl+C 두 번 사이 허용 간격 */
const EXIT_WINDOW_MS = 2000;

export const HELP_TEXT = [
  "/mode <id>      switch mode",
  "/model <name>   switch model",
  "/sessions       list saved sessions",
  "/clear          clear the screen",
  "/help           show this help",
  "/quit           exit",
  "Ctrl+C cancels a running turn; press twice when idle to exit. Ctrl+O toggles tool output.",
  "End a line with \\ to continue on the next line.",
].join("\n");

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function App({ handle, listSessions }: AppProps) {
  const { exit } = useApp();
  const { write } = useStdout();
  const [state, dispatch] = useReducer(reduceUi, handle.info, initialState);
  const [permissions, setPermissions] = useState<PendingPermission[]>([]);
  const [expandAll, setExpandAll] = useState(false);
  const [exitArmedAt, setExitArmedAt] = useState<number | null>(null);
  const runningRef = useRef(false);
  runningRef.current = state.running;

  useEffect(() => handle.onEvent(dispatch), [handle]);

  useEffect(
    () =>
      handle.onPermission(
        (request) =>
          new Promise<PermissionAnswer>((resolve) => {
            setPermissions((q) => [...q, { request, resolve }]);
          }),
      ),
    [handle],
  );

  useEffect(() => {
    if (exitArmedAt === null) return;
    const t = setTimeout(() => setExitArmedAt(null), EXIT_WINDOW_MS);
    return () => clearTimeout(t);
  }, [exitArmedAt]);

  const notice = useCallback((text: string, error = false) => dispatch({ type: "ui_notice", text, error }), []);

  const answerPermission = useCallback((answer: PermissionAnswer) => {
    setPermissions((q) => {
      q[0]?.resolve(answer);
      return q.slice(1);
    });
  }, []);

  const runSlash = useCallback(
    async (line: string) => {
      const [cmd, ...rest] = line.trim().split(/\s+/);
      const arg = rest.join(" ");
      const info = handle.info;
      switch (cmd) {
        case "/mode": {
          if (!arg) {
            notice(`modes: ${info.modes.map((m) => (m.id === state.mode ? `*${m.id}` : m.id)).join(", ")}`);
            return;
          }
          if (info.modes.length > 0 && !info.modes.some((m) => m.id === arg)) {
            notice(`unknown mode: ${arg}`, true);
            return;
          }
          await handle.setMode(arg);
          dispatch({ type: "mode_changed", modeId: arg });
          return;
        }
        case "/model": {
          if (!arg) {
            const list = info.models.map((m) => (m.value === state.model ? `*${m.value}` : m.value));
            notice(list.length > 0 ? `models: ${list.join(", ")}` : `model: ${state.model ?? "default"}`);
            return;
          }
          const match = info.models.find((m) => m.value === arg || m.name === arg);
          if (info.models.length > 0 && !match) {
            notice(`unknown model: ${arg}`, true);
            return;
          }
          const value = match?.value ?? arg;
          await handle.setModel(value);
          dispatch({ type: "model_changed", model: value });
          return;
        }
        case "/sessions": {
          if (!listSessions) {
            notice("session listing is not available", true);
            return;
          }
          const items = await listSessions();
          if (items.length === 0) {
            notice("no saved sessions");
            return;
          }
          const lines = items.map(
            (s) => `${s.sessionId.slice(0, 8)}  ${s.updatedAt ?? ""}  ${s.title ?? s.cwd}`.trimEnd(),
          );
          notice(`${lines.join("\n")}\nresume with: perdix --resume <id>`);
          return;
        }
        case "/clear":
          write("\x1b[2J\x1b[3J\x1b[H");
          return;
        case "/help":
          notice(HELP_TEXT);
          return;
        case "/quit":
        case "/exit":
          exit();
          return;
        default:
          notice(`unknown command: ${cmd ?? ""} (try /help)`, true);
      }
    },
    [exit, handle, listSessions, notice, state.mode, state.model, write],
  );

  const submit = useCallback(
    (text: string) => {
      if (text.trim().startsWith("/")) {
        runSlash(text).catch((err: unknown) => notice(errorMessage(err), true));
        return;
      }
      if (runningRef.current) {
        notice("a turn is already running (Ctrl+C to cancel)", true);
        return;
      }
      if (state.serverExited) {
        notice("server has exited; restart perdix", true);
        return;
      }
      dispatch({ type: "user_text", text });
      handle.prompt(text).then(
        () => dispatch({ type: "ui_idle" }),
        (err: unknown) => {
          dispatch({ type: "error", message: errorMessage(err) });
          dispatch({ type: "ui_idle" });
        },
      );
    },
    [handle, notice, runSlash, state.serverExited],
  );

  useInput((input, key) => {
    if (key.ctrl && input === "c") {
      if (state.running) {
        // 대기 중인 권한 요청은 취소로 응답하고 턴을 취소한다.
        setPermissions((q) => {
          for (const p of q) p.resolve({ cancelled: true });
          return [];
        });
        handle.cancel().catch((err: unknown) => notice(errorMessage(err), true));
        return;
      }
      if (exitArmedAt !== null && Date.now() - exitArmedAt < EXIT_WINDOW_MS) {
        exit();
        return;
      }
      setExitArmedAt(Date.now());
      return;
    }
    if (key.ctrl && input === "o") {
      setExpandAll((v) => !v);
      return;
    }
    if (exitArmedAt !== null) setExitArmedAt(null);
  });

  const pending = permissions[0];
  return (
    <Box flexDirection="column">
      <Transcript done={state.done} live={state.live} expandAll={expandAll} />
      {pending && <PermissionPrompt key={pending.request.requestId} request={pending.request} onAnswer={answerPermission} />}
      <InputBox active={!pending} onSubmit={submit} placeholder="Type a message or /help" />
      <StatusBar
        mode={state.mode}
        model={state.model}
        usage={state.usage}
        sessionId={state.sessionId}
        running={state.running}
        hint={exitArmedAt !== null ? "Press Ctrl+C again to exit" : null}
      />
    </Box>
  );
}
