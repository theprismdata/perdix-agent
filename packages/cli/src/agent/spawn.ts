/**
 * Python 에이전트 서버(perdix-server) 발견과 프로세스 기동.
 * 발견 순서: 명시적 지정 → 동봉 바이너리 → PATH → 동봉 소스 → 저장소 개발 모드.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { accessSync, constants, existsSync, mkdirSync, openSync, closeSync, realpathSync } from "node:fs";
import { homedir } from "node:os";
import { delimiter, dirname, join, resolve } from "node:path";
import { Readable, Writable } from "node:stream";
import { fileURLToPath } from "node:url";

export interface ServerCommand {
  command: string;
  args: string[];
  /** 어떤 경로로 찾았는지(진단 메시지용) */
  source: "flag" | "env" | "path" | "bundled" | "dev";
}

export interface ResolveOptions {
  /** `--server <cmd>` 값. 공백 구분 명령 */
  server?: string;
  env?: NodeJS.ProcessEnv;
  cwd?: string;
  /** 저장소 루트 탐색 시작점. 기본은 이 모듈의 위치 */
  moduleDir?: string;
  /** 독립 실행 바이너리 위치. */
  executableDir?: string;
  /** 찾을 실행 파일 이름. 기본 "perdix-server" (게이트웨이 등 다른 바이너리를 찾을 때 지정) */
  binaryName?: string;
}

/** 공백 구분 명령 문자열을 실행 파일과 인자로 나눈다. */
export function splitCommand(text: string): { command: string; args: string[] } {
  const parts = text.trim().split(/\s+/).filter((p) => p.length > 0);
  const [command, ...args] = parts;
  if (!command) throw new Error("empty server command");
  return { command, args };
}

function isExecutable(file: string): boolean {
  try {
    accessSync(file, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/** PATH에서 실행 파일을 찾는다. */
export function findOnPath(name: string, env: NodeJS.ProcessEnv = process.env): string | null {
  const dirs = (env.PATH ?? "").split(delimiter).filter((d) => d.length > 0);
  const exts = process.platform === "win32" ? (env.PATHEXT ?? ".EXE;.CMD;.BAT").split(";") : [""];
  for (const dir of dirs) {
    for (const ext of exts) {
      const candidate = join(dir, name + ext);
      if (isExecutable(candidate)) return candidate;
    }
  }
  return null;
}

/** start에서 위로 올라가며 `server/pyproject.toml`이 있는 저장소 루트를 찾는다. */
export function findRepoRoot(start: string): string | null {
  let dir = resolve(start);
  for (;;) {
    if (existsSync(join(dir, "server", "pyproject.toml"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

export function resolveServerCommand(opts: ResolveOptions = {}): ServerCommand {
  const env = opts.env ?? process.env;
  if (opts.server && opts.server.trim()) {
    return { ...splitCommand(opts.server), source: "flag" };
  }
  const fromEnv = env.PERDIX_SERVER;
  if (fromEnv && fromEnv.trim()) {
    return { ...splitCommand(fromEnv), source: "env" };
  }
  const binaryName = opts.binaryName ?? "perdix-server";
  const nativeServer = join(opts.executableDir ?? dirname(realpathSync(process.execPath)), "server", process.platform === "win32" ? `${binaryName}.exe` : binaryName);
  if (existsSync(nativeServer)) return { command: nativeServer, args: [], source: "bundled" };
  const onPath = findOnPath(binaryName, env);
  if (onPath) return { command: onPath, args: [], source: "path" };

  const moduleDir = opts.moduleDir ?? dirname(fileURLToPath(import.meta.url));
  const bundledServer = resolve(moduleDir, "..", "server");
  if (existsSync(join(bundledServer, "pyproject.toml"))) {
    const uv = findOnPath("uv", env);
    if (!uv) throw new Error("Bundled perdix server requires uv. Install uv and add it to PATH.");
    return {
      command: uv,
      args: ["run", "--locked", "--no-dev", "--project", bundledServer, binaryName],
      source: "bundled",
    };
  }
  const repo = findRepoRoot(moduleDir) ?? findRepoRoot(opts.cwd ?? process.cwd());
  if (repo) {
    return {
      command: "uv",
      args: ["run", "--project", join(repo, "server"), binaryName],
      source: "dev",
    };
  }
  throw new Error(
    `${binaryName} not found. Install it (\`uv tool install perdix\`), set PERDIX_SERVER, or pass --server <cmd>.`,
  );
}

export interface SpawnOptions {
  /** 서버 argv 뒤에 덧붙일 인자(예: --permission-mode deny) */
  extraArgs?: string[];
  /** true면 서버 stderr를 CLI stderr로 그대로 흘린다. 아니면 로그 파일 */
  debug?: boolean;
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  /** 로그 디렉터리. 기본 ~/.perdix/logs */
  logDir?: string;
}

export interface ServerProcess {
  readonly child: ChildProcess;
  readonly command: ServerCommand;
  /** 서버 stdin(쓰기)·stdout(읽기)의 web stream */
  readonly input: WritableStream<Uint8Array>;
  readonly output: ReadableStream<Uint8Array>;
  /** 서버 종료 시 종료 코드로 resolve(시그널·기동 실패는 null) */
  readonly exited: Promise<number | null>;
  /** 기동 실패 등 프로세스 오류(없으면 null) */
  spawnError(): Error | null;
  readonly logPath: string | null;
  kill(): void;
}

export function spawnServer(command: ServerCommand, opts: SpawnOptions = {}): ServerProcess {
  let logPath: string | null = null;
  let stderrTarget: "inherit" | number = "inherit";
  if (!opts.debug) {
    const logDir = opts.logDir ?? join(homedir(), ".perdix", "logs");
    try {
      mkdirSync(logDir, { recursive: true });
      logPath = join(logDir, `cli-${process.pid}.log`);
      stderrTarget = openSync(logPath, "a");
    } catch {
      // 로그 파일을 못 만들면 stderr로 흘린다.
      logPath = null;
      stderrTarget = "inherit";
    }
  }

  const child = spawn(command.command, [...command.args, ...(opts.extraArgs ?? [])], {
    cwd: opts.cwd,
    env: opts.env ?? process.env,
    stdio: ["pipe", "pipe", stderrTarget],
    // VS Code 같은 GUI 호스트에서도 ACP 서버의 콘솔 창을 띄우지 않는다.
    windowsHide: true,
  });
  if (typeof stderrTarget === "number") closeSync(stderrTarget);

  let error: Error | null = null;
  const exited = new Promise<number | null>((resolveExit) => {
    child.once("error", (err) => {
      error = err;
      resolveExit(null);
    });
    child.once("exit", (code) => resolveExit(code));
  });
  // 서버가 먼저 죽으면 EPIPE가 날 수 있다. 종료 감지는 exited로 한다.
  child.stdin?.on("error", () => {});
  child.stdout?.on("error", () => {});

  if (!child.stdin || !child.stdout) throw new Error("server stdio is not piped");
  const input = Writable.toWeb(child.stdin) as WritableStream<Uint8Array>;
  const output = Readable.toWeb(child.stdout) as ReadableStream<Uint8Array>;

  return {
    child,
    command,
    input,
    output,
    exited,
    spawnError: () => error,
    logPath,
    kill: () => {
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
    },
  };
}
