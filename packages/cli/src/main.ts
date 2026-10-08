#!/usr/bin/env node
import { resolve } from "node:path";
import { createInterface } from "node:readline/promises";
import { parseArgs } from "node:util";
import { runAuthCommand } from "./agent/auth.js";
import { startServerConnection, type PerdixConnection } from "./agent/connection.js";
import { formatEventText, runHeadless } from "./agent/headless.js";
import type { AgentSession } from "./agent/session.js";
import type { SessionHandle, SessionListItem } from "./agent/types.js";
import { checkNodeVersion, VERSION } from "./version.js";

const USAGE = `Usage: perdix [options]
       perdix auth <login|status|logout>   계정 로그인 관리 (perdix auth help)

Options:
  -p, --prompt <text>        Run one prompt non-interactively and exit
      --json                 With -p: print events as NDJSON
      --cwd <dir>            Working directory (default: current directory)
      --mode <id>            Session mode (chat | plan | build)
      --model <name>         Model id
      --resume <sessionId>   Resume a saved session
      --list                 List saved sessions for the working directory
      --permission-mode <m>  interactive | deny | accept-edits | allowlist (default with -p: deny)
      --server <cmd>         Server command (overrides PERDIX_SERVER)
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
      model: { type: "string" },
      resume: { type: "string" },
      list: { type: "boolean", default: false },
      "permission-mode": { type: "string" },
      server: { type: "string" },
      debug: { type: "boolean", default: false },
      version: { type: "boolean", short: "v", default: false },
      help: { type: "boolean", short: "h", default: false },
    },
  }).values;
}

/** ui/ 모듈은 지연 로드한다. 빌드에 없으면 null */
async function loadUi(): Promise<RenderAppModule | null> {
  // 지정자를 변수로 두어 번들러가 정적으로 따라가지 않게 한다(tsup가 ui/index를 별도 엔트리로 낸다).
  const specifier = new URL("./ui/index.js", import.meta.url).href;
  try {
    const mod = (await import(specifier)) as Partial<RenderAppModule>;
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

  const cwd = resolve(args.cwd ?? process.cwd());
  const permissionMode = args["permission-mode"];

  if (args.prompt !== undefined) {
    return runHeadless({
      prompt: args.prompt,
      json: args.json,
      cwd,
      mode: args.mode,
      model: args.model,
      resume: args.resume,
      permissionMode: permissionMode ?? "deny",
      server: args.server,
      debug: args.debug,
    });
  }

  let connection: PerdixConnection | null = null;
  let session: AgentSession | null = null;
  try {
    connection = await startServerConnection({
      cwd,
      server: args.server,
      debug: args.debug,
      permissionMode,
    });

    if (args.list) {
      const items = await connection.listSessions(cwd);
      if (items.length === 0) process.stdout.write(`No sessions for ${cwd}\n`);
      for (const s of items) {
        process.stdout.write(`${s.sessionId}  ${s.updatedAt ?? "-"}  ${s.title ?? ""}\n`);
      }
      return 0;
    }

    session = args.resume
      ? await connection.loadSession({ sessionId: args.resume, cwd })
      : await connection.newSession({ cwd, mode: args.mode, model: args.model });
    if (args.resume && args.mode) await session.setMode(args.mode);
    if (args.resume && args.model) await session.setModel(args.model);

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
