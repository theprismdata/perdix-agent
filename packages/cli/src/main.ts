#!/usr/bin/env node
import { resolve } from "node:path";
import { createInterface } from "node:readline/promises";
import { parseArgs } from "node:util";
import { runAuthCommand } from "./agent/auth.js";
import { startServerConnection, type PerdixConnection } from "./agent/connection.js";
import { DEFAULT_CREDENTIALS_PATH, ensureCredentialsFile } from "./agent/credentials.js";
import { formatEventText, runHeadless } from "./agent/headless.js";
import type { AgentSession } from "./agent/session.js";
import type { SessionHandle, SessionListItem } from "./agent/types.js";
import { checkNodeVersion, VERSION } from "./version.js";

const USAGE = `Usage: perdix [options]
       perdix auth <login|status|logout|list|use>   인증 프로필 관리 (perdix auth help)

Options:
  -p, --prompt <text>        Run one prompt non-interactively and exit
      --json                 With -p: print events as NDJSON
      --cwd <dir>            Working directory (default: current directory)
      --mode <id>            Session mode (general | plan | build)
      --provider <name>      anthropic | openai | gemini | openrouter | ollama
      --model <name>         Model id
      --ollama-base-url <url> Ollama OpenAI-compatible endpoint
      --agents-config <path> Role model assignment file
      --team <name>          로컬 협업 팀 이름
      --member <name>        이 터미널의 구성원 이름
      --team-role <role>     leader | member (기본 member)
      --team-worker          팀 메시지를 기다려 자동 실행
      --team-project <path>  worktree들이 공유하는 프로젝트 경로
      --team-db <path>       공유 팀 SQLite 경로
      --resume <sessionId>   Resume a saved session
      --tui <name>          default | hermes (Hermes TUI 평가판)
      --hermes-desktop      Hermes Desktop 앱이 접속할 호환 서버를 실행(주소·토큰 출력)
      --list                 List saved sessions for the working directory
      --permission-mode <m>  interactive | deny | accept-edits | allowlist | yolo (default with -p: deny)
      --server <cmd>         Server command (overrides PERDIX_SERVER)
      --trust-project        Trust verification commands in the working directory
      --debug                Pass server stderr through instead of logging to ~/.perdix/logs
  -v, --version              Print version
  -h, --help                 Show this help
`;

interface RenderAppModule {
  renderApp: (
    handle: SessionHandle,
    opts: { listSessions?: () => Promise<SessionListItem[]> },
  ) => Promise<void>;
}

function parseCli(argv: string[]) {
  return parseArgs({
    args: argv,
    allowPositionals: false,
    strict: true,
    options: {
      prompt: { type: "string", short: "p" },
      json: { type: "boolean", default: false },
      cwd: { type: "string" },
      mode: { type: "string" },
      provider: { type: "string" },
      model: { type: "string" },
      "ollama-base-url": { type: "string" },
      "agents-config": { type: "string" },
      team: { type: "string" },
      member: { type: "string" },
      "team-role": { type: "string" },
      "team-worker": { type: "boolean", default: false },
      "team-project": { type: "string" },
      "team-db": { type: "string" },
      resume: { type: "string" },
      tui: { type: "string", default: "default" },
      "hermes-desktop": { type: "boolean", default: false },
      list: { type: "boolean", default: false },
      "permission-mode": { type: "string" },
      "trust-project": { type: "boolean", default: false },
      server: { type: "string" },
      debug: { type: "boolean", default: false },
      version: { type: "boolean", short: "v", default: false },
      help: { type: "boolean", short: "h", default: false },
    },
  }).values;
}

/** ui/ 모듈은 지연 로드한다. 독립 바이너리에도 포함된다. */
async function loadUi(): Promise<RenderAppModule | null> {
  try {
    // 정적 지정자로 바이너리 빌드에도 TUI와 의존성을 포함한다.
    const mod = (await import("./ui/index.js")) as Partial<RenderAppModule>;
    return typeof mod.renderApp === "function" ? (mod as RenderAppModule) : null;
  } catch (err) {
    const code = (err as { code?: string }).code;
    const message = err instanceof Error ? err.message : "";
    // ui/index 자체가 없을 때만 폴백한다. ui 내부의 의존성 누락은 그대로 던진다.
    if (code === "ERR_MODULE_NOT_FOUND" && message.includes("ui/index.js")) return null;
    throw err;
  }
}

/** UI 모듈이 없을 때의 임시 텍스트 REPL */
async function runFallbackRepl(session: AgentSession): Promise<void> {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  session.onEvent((event) => {
    if (event.type === "error") process.stderr.write(`perdix: ${event.message}\n`);
    else process.stdout.write(formatEventText(event));
  });
  session.onPermission(async (req) => {
    const choices = req.options.map((o, i) => `${i + 1}) ${o.name}`).join("  ");
    const answer = await rl.question(`\n[permission] ${req.title}\n${choices}\n> `);
    const picked = req.options[Number.parseInt(answer, 10) - 1];
    return picked ? { optionId: picked.optionId } : { cancelled: true };
  });
  process.stdout.write(
    `perdix ${VERSION} — session ${session.info.sessionId.slice(0, 8)} (${session.info.currentMode}). /quit to exit.\n`,
  );
  for (;;) {
    let line: string;
    try {
      line = (await rl.question("\n> ")).trim();
    } catch {
      break; // stdin 종료
    }
    if (line === "/quit" || line === "/exit") break;
    if (!line) continue;
    await session.prompt(line);
  }
  rl.close();
}

async function main(): Promise<number> {
  const versionError = checkNodeVersion(process.versions.node);
  if (versionError) {
    process.stderr.write(`${versionError}\n`);
    return 1;
  }

  if (process.argv[2] === "auth") {
    return runAuthCommand(process.argv.slice(3));
  }

  let args: ReturnType<typeof parseCli>;
  try {
    args = parseCli(process.argv.slice(2));
  } catch (err) {
    process.stderr.write(`perdix: ${err instanceof Error ? err.message : String(err)}\n\n${USAGE}`);
    return 2;
  }
  if (args.version) {
    process.stdout.write(`perdix ${VERSION}\n`);
    return 0;
  }
  if (args.help) {
    process.stdout.write(USAGE);
    return 0;
  }
  if (args.tui !== "default" && args.tui !== "hermes") {
    process.stderr.write("perdix: --tui는 default 또는 hermes여야 합니다.\n");
    return 2;
  }
  if (args.provider !== undefined
      && !["anthropic", "openai", "gemini", "openrouter", "ollama"].includes(args.provider)) {
    process.stderr.write("perdix: 지원하지 않는 provider입니다.\n");
    return 2;
  }
  if (args.tui === "hermes" && !args["team-worker"] && !args.list
      && (args.prompt !== undefined || !process.stdin.isTTY)) {
    process.stderr.write("perdix: Hermes 평가판은 대화형 터미널에서 실행하세요. -p와 함께 사용할 수 없습니다.\n");
    return 2;
  }

  const cwd = resolve(args.cwd ?? process.cwd());
  if (Boolean(args.team) !== Boolean(args.member)
      || (args["team-worker"] && !args.team)
      || (args["team-role"] !== undefined && !["leader", "member"].includes(args["team-role"]))
      || (args["team-worker"] && (args.resume !== undefined
        || args.list))) {
    process.stderr.write("perdix: --team/--member를 함께 지정하세요. --team-worker는 --resume/--list와 함께 사용할 수 없습니다.\n");
    return 2;
  }
  try {
    if (ensureCredentialsFile()) {
      process.stderr.write(`API 키 설정 파일을 생성했습니다: ${DEFAULT_CREDENTIALS_PATH}\n사용할 공급자의 api_key에 키를 입력하세요.\n`);
    }
  } catch (err) {
    // 환경변수 인증은 파일 쓰기 실패와 무관하게 사용할 수 있다.
    process.stderr.write(`설정 파일 생성 실패 (${DEFAULT_CREDENTIALS_PATH}): ${err instanceof Error ? err.message : String(err)}\n`);
  }
  const permissionMode = args["permission-mode"];
  const serverArgs = args["agents-config"] ? ["--agents-config", resolve(args["agents-config"])] : [];
  if (args.provider) serverArgs.push("--provider", args.provider);
  if (args["ollama-base-url"]) {
    serverArgs.push("--ollama-base-url", args["ollama-base-url"]);
  }
  if (args.team && args.member) {
    serverArgs.push("--team", args.team, "--member", args.member,
      "--team-role", args["team-role"] ?? "member");
    if (args["team-worker"]) serverArgs.push("--team-worker");
    if (args["team-project"]) serverArgs.push("--team-project", resolve(args["team-project"]));
    if (args["team-db"]) serverArgs.push("--team-db", resolve(args["team-db"]));
  }
  const model = args.model;

  if (args.prompt !== undefined || args["team-worker"]) {
    return runHeadless({
      prompt: args.prompt ?? "",
      teamWorker: args["team-worker"],
      team: args.team !== undefined,
      json: args.json,
      cwd,
      mode: args.mode,
      model,
      resume: args.resume,
      permissionMode: permissionMode ?? "deny",
      trustProject: args["trust-project"],
      server: args.server,
      serverArgs,
      debug: args.debug,
    });
  }

  let connection: PerdixConnection | null = null;
  let session: AgentSession | null = null;
  try {
    connection = await startServerConnection({
      cwd,
      server: args.server,
      serverArgs,
      debug: args.debug,
      permissionMode,
      trustProject: args["trust-project"],
    });

    if (args.list) {
      const items = await connection.listSessions(cwd);
      if (items.length === 0) process.stdout.write(`No sessions for ${cwd}\n`);
      for (const s of items) {
        process.stdout.write(`${s.sessionId}  ${s.updatedAt ?? "-"}  ${s.title ?? ""}\n`);
      }
      return 0;
    }

    if (args["hermes-desktop"]) {
      const [{ HermesAdapter }, { startHermesDesktopServer }] = await Promise.all([
        import("./hermes/adapter.js"), import("./hermes/desktop-server.js"),
      ]);
      const adapter = new HermesAdapter(connection, { cwd, mode: args.mode, model });
      const desktop = await startHermesDesktopServer(adapter, {
        port: Number(process.env.PERDIX_HERMES_DESKTOP_PORT) || undefined, cwd,
        token: process.env.PERDIX_HERMES_DESKTOP_TOKEN || undefined,
        log: (line) => { if (args.debug) process.stderr.write(`[hermes-desktop] ${line}\n`); },
      });
      process.stdout.write(`HERMES_DESKTOP_REMOTE_URL=${desktop.url}\n`);
      if (process.env.PERDIX_HERMES_DESKTOP_SHOW_TOKEN === "1") process.stdout.write(`HERMES_DESKTOP_REMOTE_TOKEN=${desktop.token}\n`);
      await new Promise<void>((resolve) => {
        process.once("SIGINT", resolve);
        process.once("SIGTERM", resolve);
      });
      await desktop.close();
      return 0;
    }

    if (args.tui === "hermes") {
      const [{ launchHermesTui }, { HermesAdapter }, { startHermesBridge }, { startMacTerminalImagePaste }] = await Promise.all([
        import("@perdix/hermes-tui"), import("./hermes/adapter.js"), import("./hermes/bridge.js"), import("./hermes/macos-image-paste.js"),
      ]);
      const { runHermesAuth } = await import("./hermes/auth.js");
      const adapter = new HermesAdapter(connection, { cwd, mode: args.mode, model,
        runAuth: (action, provider, signal) => runHermesAuth(action, provider, signal, args.server),
        reconnect: async (signal) => {
          const next = await startServerConnection({ cwd, server: args.server, serverArgs, debug: args.debug, permissionMode, trustProject: args["trust-project"] });
          if (signal.aborted) { await next.close(); throw new Error("인증 후 연결을 취소했습니다."); }
          const previous = connection;
          connection = next;
          await previous?.close();
          return next;
        },
      });
      const bridge = await startHermesBridge(adapter);
      let imagePaste: { close(): Promise<void> } | undefined;
      try {
        imagePaste = await startMacTerminalImagePaste();
        return await launchHermesTui({ gatewayUrl: bridge.url, cwd, resume: args.resume, terminalHandoff: () => adapter.authenticating });
      } finally {
        await imagePaste?.close();
        await bridge.close();
      }
    }

    session = args.resume
      ? await connection.loadSession({ sessionId: args.resume, cwd })
      : await connection.newSession({ cwd, mode: args.mode, model });
    if (args.resume && args.mode) await session.setMode(args.mode);
    if (args.resume && args.model) await session.setModel(args.model);
    if (args.team) {
      const joined = await session.prompt("/team status");
      if (joined.stopReason !== "end_turn") return 1;
    }

    // Ink는 raw mode TTY가 필요하다. 파이프 입력이면 텍스트 REPL로 대신한다.
    const ui = process.stdin.isTTY ? await loadUi() : null;
    const conn = connection;
    if (ui) {
      await ui.renderApp(session, { listSessions: () => conn.listSessions(cwd) });
    } else {
      await runFallbackRepl(session);
    }
    return 0;
  } catch (err) {
    process.stderr.write(`perdix: ${err instanceof Error ? err.message : String(err)}\n`);
    return 1;
  } finally {
    if (session && args.team) await session.prompt("/team leave");
    if (session) await session.close();
    else if (connection) await connection.close();
  }
}

main().then(
  (code) => {
    process.exitCode = code;
  },
  (err: unknown) => {
    process.stderr.write(`perdix: ${err instanceof Error ? (err.stack ?? err.message) : String(err)}\n`);
    process.exitCode = 1;
  },
);
