import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { Box, Text, useApp, useInput, useStdout } from "ink";
import { theme } from "./theme.js";
import type {
  PermissionAnswer,
  PermissionRequest,
  SessionHandle,
  SessionInfo,
  SessionListItem,
} from "../agent/types.js";
import { initialState, reduceUi } from "./state.js";
import { Transcript } from "./components/Transcript.js";
import { PermissionPrompt } from "./components/PermissionPrompt.js";
import { InputBox, type SlashCommandHint } from "./components/InputBox.js";
import { StatusBar } from "./components/StatusBar.js";
import { RequestProgress } from "./components/RequestProgress.js";
import { Banner } from "./components/Banner.js";

export interface AppProps {
  handle: SessionHandle;
  listSessions?: () => Promise<SessionListItem[]>;
  /**
   * `/login`, `/logout` 실행기. 터미널을 직접 써야 하므로 호출자(renderApp)가 TUI를
   * 내렸다가 다시 띄운다. 종료 코드를 돌려준다. 없으면 두 명령은 비활성.
   */
  onRunAuth?: (args: string[]) => Promise<number>;
  /** 마운트 직후 보여줄 알림. 재마운트 뒤 인증 결과를 띄우는 데 쓴다. */
  initialNotice?: { text: string; error: boolean };
}

interface PendingPermission {
  request: PermissionRequest;
  resolve: (answer: PermissionAnswer) => void;
}

/** Ctrl+C 두 번 사이 허용 간격 */
const EXIT_WINDOW_MS = 2000;

export const SLASH_COMMANDS: SlashCommandHint[] = [
  { name: "/workflow", hint: "plan <request> | start <runId> | status <runId> | resume <runId> | retry <runId> <taskId> | accept <runId> | reject <runId>" },
  { name: "/team", hint: "팀 안내·상태 조회·종료" },
  { name: "/memories", hint: "자동 메모리 상태·생성·참조 설정" },
  { name: "/auth", hint: "status | list | use <제공자> <프로필|legacy>" },
  { name: "/permissions", hint: "interactive | yolo | deny | accept-edits | allowlist" },
  { name: "/loop", hint: "<목표> 실행·검증·수정 반복" },
  { name: "/agent", hint: "<id> switch agent" },
  { name: "/model", hint: "<name> switch model" },
  { name: "/sessions", hint: "list saved sessions" },
  { name: "/login", hint: "OpenAI·Claude 선택 후 로그인" },
  { name: "/logout", hint: "sign out" },
  { name: "/clear", hint: "clear the screen" },
  { name: "/help", hint: "show this help" },
  { name: "/quit", hint: "exit" },
];

/** 도움말 한 줄: 인자 표기(`<...>`)까지 16칸에 맞춰 정렬한다. */
function helpLine({ name, hint }: SlashCommandHint): string {
  const match = /^(<[^>]+>) (.*)$/.exec(hint);
  const usage = match ? `${name} ${match[1]}` : name;
  return `${usage.padEnd(15)} ${match ? match[2] : hint}`;
}

export const HELP_TEXT = [
  ...SLASH_COMMANDS.map(helpLine),
  "Ctrl+C cancels a running turn; press twice when idle to exit. Ctrl+O toggles tool output.",
  "End a line with \\ to continue on the next line.",
].join("\n");

/** `/agent`, `/model` 에 현재 세션이 허용하는 인자 후보를 붙인다. */
function slashCommandsWithArgs(info: SessionInfo): SlashCommandHint[] {
  return SLASH_COMMANDS.map((c) => {
    if (c.name === "/agent") return { ...c, args: info.modes.map((m) => m.id) };
    if (c.name === "/model") return { ...c, args: info.models.map((m) => m.value) };
    if (c.name === "/loop") return { ...c, args: ["status", "cancel", "resume"] };
    if (c.name === "/team") return { ...c, args: ["status", "stop", "leave", "help"] };
    if (c.name === "/workflow") return { ...c, args: ["plan", "start", "status", "resume", "retry", "accept", "reject"] };
    return c;
  });
}

/** `/login`, `/logout` 결과 알림. 재마운트된 App에서도 같은 문구를 쓰도록 공유한다. */
export function authResultNotice(
  action: string,
  code: number,
  context?: { args: string[]; info: SessionInfo },
): { text: string; error: boolean } {
  if (action === "login" && code === 0 && context) {
    const providerIndex = context.args.indexOf("--provider");
    const provider = providerIndex >= 0 ? context.args[providerIndex + 1] : "anthropic";
    const prefix = provider === "openai" ? "gpt-" : provider === "gemini" ? "gemini-" : "claude-";
    const model = context.info.models.find((m) => m.provider === provider || (!m.provider &&
      (provider === "openrouter" ? m.value.includes("/") : m.value.startsWith(prefix))));
    const name = provider === "openai" ? "OpenAI" : provider === "gemini" ? "Gemini" : provider === "openrouter" ? "OpenRouter" : "Claude";
    const command = model ? `/model ${model.value}` : "/model";
    return {
      text: `login succeeded\n로그인은 인증만 설정하며 대화 모델은 바꾸지 않습니다. 현재 모델: ${context.info.currentModel ?? "default"}\n${name} 모델을 사용하려면 ${command} 명령으로 전환하세요.`,
      error: false,
    };
  }
  return code === 0
    ? { text: `${action} succeeded`, error: false }
    : { text: `${action} failed (exit code ${code})`, error: true };
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function App({ handle, listSessions, onRunAuth, initialNotice }: AppProps) {
  const { exit } = useApp();
  const { write } = useStdout();
  const [state, dispatch] = useReducer(reduceUi, handle.info, initialState);
  const [permissions, setPermissions] = useState<PendingPermission[]>([]);
  const [expandAll, setExpandAll] = useState(false);
  const [loginProvider, setLoginProvider] = useState<"openai" | "anthropic" | null>(null);
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

  // 마운트 시 한 번만 보여준다.
  const initialNoticeRef = useRef(initialNotice);
  useEffect(() => {
    const n = initialNoticeRef.current;
    if (n) notice(n.text, n.error);
  }, [notice]);

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
        case "/workflow":
          if (runningRef.current) {
            notice("작업이 끝난 뒤 workflow 명령을 실행하세요. 취소는 Ctrl+C입니다.", true);
            return;
          }
          await handle.prompt(line);
          return;
        case "/team":
          if (runningRef.current) {
            notice("작업이 끝난 뒤 팀 명령을 실행하세요. 취소는 Ctrl+C입니다.", true);
            return;
          }
          await handle.prompt(line);
          return;
        case "/auth":
        case "/permissions":
        case "/memories":
          if (runningRef.current) {
            notice("작업이 끝난 뒤 메모리 설정을 변경하세요.", true);
            return;
          }
          await handle.prompt(line);
          return;
        case "/loop": {
          const control = !arg || arg === "status" || arg === "cancel";
          if (runningRef.current && !control) {
            notice("목표 루프가 실행 중입니다. /loop cancel로 중지하세요.", true);
            return;
          }
          if (!control) dispatch({ type: "user_text", text: line });
          await handle.prompt(line);
          if (!control) dispatch({ type: "ui_idle" });
          return;
        }
        case "/agent": {
          if (!arg) {
            notice(`agents: ${info.modes.map((m) => (m.id === state.mode ? `*${m.id}` : m.id)).join(", ")}`);
            return;
          }
          if (info.modes.length > 0 && !info.modes.some((m) => m.id === arg)) {
            notice(`unknown agent: ${arg}`, true);
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
        case "/login":
        case "/logout": {
          if (!onRunAuth) {
            notice("login is not available in this context", true);
            return;
          }
          if (runningRef.current) {
            notice("a turn is running; cancel it (Ctrl+C) before signing in", true);
            return;
          }
          if (cmd === "/login" && rest.length === 0) {
            setLoginProvider("openai");
            return;
          }
          // onRunAuth가 끝날 때까지 TUI는 화면에서 내려가고 ant/키 입력 프롬프트가 터미널을
          // 직접 쓴다. 의도된 동작이므로 "로그인 중" 같은 Ink 알림을 먼저 띄우지 않는다.
          // 반영 시점: Anthropic(ant 프로필)은 해당 모델로 아직 메시지를 보내지 않았다면 다음
          // 메시지부터 적용된다(서버가 모델별 provider/SDK 클라이언트를 처음 쓸 때 지연 생성하며
          // 자격 증명을 찾는다). 이미 쓴 모델은 클라이언트가 캐시되어 서버 재시작 전까지 그대로다.
          // OpenAI(--provider openai)는 서버가 시작 시 ~/.perdix/credentials.json을 한 번만
          // 읽고 다시 읽지 않으므로, 새로 저장한 키는 서버(perdix)를 재시작해야 적용된다.
          const action = cmd.slice(1);
          const code = await onRunAuth([action, ...rest]);
          const result = authResultNotice(action, code, { args: [action, ...rest], info: handle.info });
          notice(result.text, result.error);
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
    [exit, handle, listSessions, notice, onRunAuth, state.mode, state.model, write],
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
      dispatch({ type: "turn_started" });
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
    if (loginProvider !== null && !permissions[0]) {
      if (key.escape || (key.ctrl && input === "c")) {
        setLoginProvider(null);
      } else if (key.tab || key.leftArrow || key.rightArrow) {
        setLoginProvider((provider) => provider === "openai" ? "anthropic" : "openai");
      } else if (key.return) {
        setLoginProvider(null);
        submit(`/login --provider ${loginProvider}`);
      }
      return;
    }
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
  const commandsWithArgs = slashCommandsWithArgs(handle.info);
  return (
    <Box flexDirection="column">
      {state.done.length === 0 && state.live.length === 0 && (
        <Banner cwd={handle.info.cwd} mode={state.mode} model={state.model} sessionId={state.sessionId} />
      )}
      <Transcript done={state.done} live={state.live} expandAll={expandAll} />
      {pending && <PermissionPrompt key={pending.request.requestId} request={pending.request} onAnswer={answerPermission} />}
      {loginProvider !== null && !pending && (
        <Box flexDirection="column" borderStyle="round" borderColor={theme.borderAccent} paddingX={1}>
          <Text>로그인할 서비스를 선택하세요</Text>
          <Box gap={2}>
            <Text inverse={loginProvider === "openai"}> OpenAI </Text>
            <Text inverse={loginProvider === "anthropic"}> Claude </Text>
          </Box>
          <Text dimColor>Tab / ← → 선택 · Enter 로그인 · Esc 취소</Text>
        </Box>
      )}
      <RequestProgress state={state} permissionTitle={pending?.request.title} />
      <InputBox active={!pending && loginProvider === null} onSubmit={submit} placeholder="Type a message or /help" commands={commandsWithArgs} />
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
