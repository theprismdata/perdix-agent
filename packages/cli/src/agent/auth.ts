/**
 * `perdix auth <action> [--provider anthropic|openai]`
 *
 * - anthropic(기본값): 공식 `ant` CLI(Anthropic CLI)에 위임한다. `ant auth login`이 저장하는
 *   OAuth 프로필은 Python SDK가 자동으로 읽으므로 서버 쪽 변경이 필요 없다.
 * - openai: OpenAI는 제3자가 재사용할 수 있는 공식 OAuth 프로필 CLI가 없다(Codex CLI의
 *   ChatGPT 로그인 토큰은 Codex 자체 백엔드로 범위가 묶여 있어 재사용이 접근 제어 우회가 된다).
 *   그래서 API 키를 직접 받아 `~/.perdix/credentials.json`에 저장하는 방식만 지원한다.
 */
import { spawn } from "node:child_process";
import { setProviderApiKey, providerStatus, removeProvider } from "./credentials.js";
import { findOnPath } from "./spawn.js";

const INSTALL_HELP = `'ant' CLI를 찾을 수 없습니다. 설치 후 다시 시도하세요.

  # macOS
  brew install anthropics/tap/ant
  xattr -d com.apple.quarantine "$(brew --prefix)/bin/ant"

  # Linux / WSL — 릴리스는 https://github.com/anthropics/anthropic-cli/releases
  curl -fsSL "https://github.com/anthropics/anthropic-cli/releases/download/v<VERSION>/ant_<VERSION>_$(uname -s | tr A-Z a-z)_$(uname -m | sed -e s/x86_64/amd64/ -e s/aarch64/arm64/).tar.gz" \\
    | sudo tar -xz -C /usr/local/bin ant

  # 또는 소스에서 (Go 1.25+)
  go install github.com/anthropics/anthropic-cli/cmd/ant@latest
`;

export const AUTH_USAGE = `Usage: perdix auth <action> [--provider anthropic|openai]

  login                       로그인/키 등록
    --provider anthropic        브라우저로 Anthropic 계정 로그인 (ant auth login 위임, 기본값)
    --provider openai [--api-key <key>]   OpenAI API 키 등록 (키 없으면 입력 프롬프트)
  status [--provider ...]     현재 활성 자격 증명 확인 (provider 생략 시 anthropic)
  logout [--provider ...]     로그인/저장된 키 해제

ANTHROPIC_API_KEY / OPENAI_API_KEY가 설정되어 있으면 저장된 프로필·키보다 항상 우선합니다.
`;

export interface RunAuthOptions {
  env?: NodeJS.ProcessEnv;
  credentialsPath?: string;
  /** 테스트용: 실제 터미널 마스킹 입력 대신 주입. */
  promptApiKey?: (label: string) => Promise<string>;
}

interface ParsedArgs {
  action: string;
  provider: "anthropic" | "openai";
  apiKey?: string;
  rest: string[];
}

function parseArgs(args: string[]): ParsedArgs {
  const action = args[0] && !args[0].startsWith("--") ? args[0] : "status";
  const rest: string[] = [];
  let provider: "anthropic" | "openai" = "anthropic";
  let apiKey: string | undefined;
  const tail = args[0] && !args[0].startsWith("--") ? args.slice(1) : args;
  for (let i = 0; i < tail.length; i++) {
    const arg = tail[i];
    if (arg === undefined) continue;
    if (arg === "--provider") {
      const value = tail[++i];
      if (value === "anthropic" || value === "openai") provider = value;
      continue;
    }
    if (arg === "--api-key") {
      apiKey = tail[++i];
      continue;
    }
    rest.push(arg);
  }
  return { action, provider, apiKey, rest };
}

async function promptApiKeyMasked(label: string): Promise<string> {
  const stdin = process.stdin;
  if (!stdin.isTTY) {
    const { createInterface } = await import("node:readline/promises");
    const rl = createInterface({ input: stdin, output: process.stdout });
    const value = await rl.question(`${label}: `);
    rl.close();
    return value.trim();
  }
  process.stdout.write(`${label}: `);
  return await new Promise<string>((resolvePromise) => {
    let value = "";
    const wasRaw = stdin.isRaw;
    stdin.setRawMode(true);
    stdin.resume();
    stdin.setEncoding("utf8");
    const onData = (chunk: string): void => {
      for (const ch of chunk) {
        if (ch === "\r" || ch === "\n") {
          stdin.setRawMode(wasRaw ?? false);
          stdin.pause();
          stdin.removeListener("data", onData);
          process.stdout.write("\n");
          resolvePromise(value.trim());
          return;
        } else if (ch === "\u0003") {
          stdin.setRawMode(wasRaw ?? false);
          process.stdout.write("\n");
          process.exit(130);
        } else if (ch === "\u007f" || ch === "\b") {
          if (value.length > 0) {
            value = value.slice(0, -1);
            process.stdout.write("\b \b");
          }
        } else {
          value += ch;
          process.stdout.write("*");
        }
      }
    };
    stdin.on("data", onData);
  });
}

async function runOpenAiAuth(
  action: string,
  apiKey: string | undefined,
  env: NodeJS.ProcessEnv,
  credentialsPath: string | undefined,
  promptApiKey: (label: string) => Promise<string>,
): Promise<number> {
  if (action === "login") {
    const key = apiKey ?? (await promptApiKey("OpenAI API key"));
    if (!key) {
      process.stderr.write("perdix: API 키가 비어 있습니다.\n");
      return 1;
    }
    setProviderApiKey("openai", key, credentialsPath);
    process.stdout.write("OpenAI API 키를 ~/.perdix/credentials.json에 저장했습니다.\n");
    return 0;
  }
  if (action === "logout") {
    const removed = removeProvider("openai", credentialsPath);
    process.stdout.write(
      removed ? "저장된 OpenAI API 키를 지웠습니다.\n" : "저장된 OpenAI API 키가 없습니다.\n",
    );
    return 0;
  }
  // status
  const source = providerStatus("openai", "OPENAI_API_KEY", env, credentialsPath);
  const message =
    source === "env"
      ? "openai: OPENAI_API_KEY 환경변수 사용 중"
      : source === "file"
        ? "openai: ~/.perdix/credentials.json에 저장된 키 사용 중"
        : "openai: 로그인되어 있지 않음 (perdix auth login --provider openai)";
  process.stdout.write(`${message}\n`);
  return 0;
}

export async function runAuthCommand(
  args: string[],
  opts: RunAuthOptions = {},
): Promise<number> {
  const env = opts.env ?? process.env;
  if (args[0] === "help" || args[0] === "--help" || args[0] === "-h") {
    process.stdout.write(AUTH_USAGE);
    return 0;
  }
  const { action, provider, apiKey, rest } = parseArgs(args);

  if (provider === "openai") {
    return runOpenAiAuth(action, apiKey, env, opts.credentialsPath, opts.promptApiKey ?? promptApiKeyMasked);
  }

  const antPath = findOnPath("ant", env);
  if (!antPath) {
    process.stderr.write(INSTALL_HELP);
    return 1;
  }
  const forwarded = ["auth", action, ...rest];
  if (!rest.includes("--profile") && env.ANTHROPIC_API_KEY) {
    process.stderr.write(
      "perdix: ANTHROPIC_API_KEY가 설정되어 있어 로그인 프로필보다 우선 적용됩니다. " +
        "프로필을 쓰려면 이 환경변수를 해제하세요.\n",
    );
  }
  return await new Promise<number>((resolvePromise, reject) => {
    const child = spawn(antPath, forwarded, { stdio: "inherit", env });
    child.once("error", reject);
    child.once("exit", (code) => resolvePromise(code ?? 1));
  });
}
