import { spawn } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

export interface HermesLaunchOptions {
  readonly gatewayUrl: string;
  readonly cwd: string;
  readonly resume?: string;
  readonly env?: NodeJS.ProcessEnv;
  readonly terminalHandoff?: () => boolean;
}

const TERMINAL_ENV = [
  "PATH", "HOME", "USER", "SHELL", "TMPDIR", "TEMP", "TMP", "SystemRoot",
  "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "COLORTERM", "LANG", "LC_ALL",
  "LC_CTYPE", "NO_COLOR", "FORCE_COLOR", "COLUMNS", "LINES",
] as const;

/** UI의 git·터미널 하위 프로세스에도 공급자 키가 전달되지 않도록 명시적으로 선별한다. */
export function hermesUiEnv(options: HermesLaunchOptions): NodeJS.ProcessEnv {
  const source = options.env ?? process.env;
  const env: NodeJS.ProcessEnv = {};
  for (const key of TERMINAL_ENV) {
    if (source[key] !== undefined) env[key] = source[key];
  }
  env.HERMES_HOME = join(homedir(), ".perdix", "hermes-tui");
  env.HERMES_TUI_PERDIX = "1";
  env.HERMES_TUI_GATEWAY_URL = options.gatewayUrl;
  env.HERMES_TUI_CWD = options.cwd;
  env.HERMES_TUI_RESUME = options.resume ?? "";
  for (const key of ["HERMES_TUI_THEME", "HERMES_TUI_MOUSE_TRACKING", "HERMES_TUI_INLINE"]) {
    if (source[key] !== undefined) env[key] = source[key];
  }
  return env;
}

/** 원본 UI의 process.exit를 부모 ACP 연결의 정리 경계 밖으로 격리한다. */
export async function launchHermesTui(options: HermesLaunchOptions): Promise<number> {
  const entry = fileURLToPath(new URL("../dist/entry.js", import.meta.url));
  if (!existsSync(entry)) throw new Error("Hermes TUI 빌드가 없습니다. pnpm --filter @perdix/hermes-tui build를 실행하세요.");
  const env = hermesUiEnv(options);
  mkdirSync(env.HERMES_HOME ?? join(homedir(), ".perdix", "hermes-tui"), { recursive: true, mode: 0o700 });
  const child = spawn(process.execPath, [entry], { cwd: options.cwd, env, stdio: "inherit" });
  const stop = () => { child.kill("SIGTERM"); };
  const interrupt = () => { if (!options.terminalHandoff?.()) stop(); };
  process.on("SIGTERM", stop);
  process.on("SIGHUP", stop);
  process.on("SIGINT", interrupt);
  try {
    return await new Promise<number>((resolve, reject) => {
      child.once("error", reject);
      child.once("close", (code) => resolve(code ?? 1));
    });
  } finally {
    process.off("SIGTERM", stop);
    process.off("SIGHUP", stop);
    process.off("SIGINT", interrupt);
    if (child.exitCode === null) child.kill("SIGTERM");
  }
}
