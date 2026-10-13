/**
 * `perdix auth <action> [--provider anthropic|openai]`
 *
 * - anthropic(기본값): 공식 `ant` CLI(Anthropic CLI)에 위임한다. `ant auth login`이 저장하는
 *   OAuth 프로필은 Python SDK가 자동으로 읽으므로 서버 쪽 변경이 필요 없다.
 * - openai: API 키가 없으면 Python 서버의 ChatGPT OAuth 로그인에 위임한다.
 */
import { spawn } from "node:child_process";
import { setProviderApiKey, providerStatus, removeProvider } from "./credentials.js";
import { findOnPath, resolveServerCommand } from "./spawn.js";

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

export const AUTH_USAGE = `Usage: perdix auth <action> [--provider anthropic|openai|gemini|openrouter]

  login                       로그인/키 등록
    --provider anthropic        브라우저로 Anthropic 계정 로그인 (ant auth login 위임, 기본값)
    --provider openai [--api-key <key>]   API 키 등록 또는 키가 없으면 ChatGPT 로그인
    --provider gemini --api-key <key>     Google AI Studio API 키 등록
    --provider openrouter --api-key <key> OpenRouter API 키 등록
  status [--provider ...]     현재 활성 자격 증명 확인 (provider 생략 시 anthropic)
  logout [--provider ...]     로그인/저장된 키 해제

ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY / OPENROUTER_API_KEY가 설정되어 있으면 저장된 프로필·키보다 항상 우선합니다.
`;

export interface RunAuthOptions {
  signal?: AbortSignal;
  onOutput?: (text: string) => void;
  env?: NodeJS.ProcessEnv;
  credentialsPath?: string;
  runServerAuth?: (action: string, env: NodeJS.ProcessEnv, credentialsPath?: string) => Promise<number>;
}

interface ParsedArgs {
  action: string;
  provider: "anthropic" | "openai" | "gemini" | "openrouter";
  apiKey?: string;
  rest: string[];
}

function parseArgs(args: string[]): ParsedArgs {
  const action = args[0] && !args[0].startsWith("--") ? args[0] : "status";
  const rest: string[] = [];
  let provider: "anthropic" | "openai" | "gemini" | "openrouter" = "anthropic";
  let apiKey: string | undefined;
  const tail = args[0] && !args[0].startsWith("--") ? args.slice(1) : args;
  for (let i = 0; i < tail.length; i++) {
    const arg = tail[i];
    if (arg === undefined) continue;
    if (arg === "--provider") {
      const value = tail[++i];
      if (value === "anthropic" || value === "openai" || value === "gemini" || value === "openrouter") provider = value;
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

async function runServerAuth(
  action: string, env: NodeJS.ProcessEnv, credentialsPath?: string,
  signal?: AbortSignal,
  onOutput?: (text: string) => void,
): Promise<number> {
  const command = resolveServerCommand({ env });
  const args = [...command.args, "--openai-auth", action];
  if (credentialsPath) args.push("--credentials", credentialsPath);
  return await new Promise<number>((resolveExit, reject) => {
    const child = spawn(command.command, args, { stdio: onOutput ? ["inherit", "pipe", "pipe"] : "inherit", env, signal });
    if (onOutput) {
      child.stdout?.setEncoding("utf8").on("data", onOutput);
      child.stderr?.setEncoding("utf8").on("data", onOutput);
    }
    child.once("error", reject);
    child.once("close", (code) => resolveExit(code ?? 1));
  });
}

async function runOpenAiAuth(
  action: string,
  apiKey: string | undefined,
  env: NodeJS.ProcessEnv,
  credentialsPath: string | undefined,
  serverAuth: NonNullable<RunAuthOptions["runServerAuth"]>,
  write: (text: string) => void,
): Promise<number> {
  if (action === "login" && apiKey !== undefined) {
    const key = apiKey.trim();
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
    return serverAuth(action, env, credentialsPath);
  }
  // status
  const source = providerStatus("openai", "OPENAI_API_KEY", env, credentialsPath);
  if (source === "none") return serverAuth(action, env, credentialsPath);
  const message =
    source === "env"
      ? "openai: OPENAI_API_KEY 환경변수 사용 중"
      : source === "file"
        ? "openai: ~/.perdix/credentials.json에 저장된 키 사용 중"
        : "openai: 로그인되어 있지 않음 (perdix auth login --provider openai)";
  write(`${message}\n`);
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

  if (provider === "gemini" || provider === "openrouter") {
    const name = provider === "openrouter" ? "OpenRouter" : "Gemini";
    const envKey = provider === "openrouter" ? "OPENROUTER_API_KEY" : "GEMINI_API_KEY";
    const keyUrl = provider === "openrouter" ? "https://openrouter.ai/settings/keys" : "https://aistudio.google.com/apikey";
    const write = opts.onOutput ?? ((text: string) => { process.stdout.write(text); });
    if (action === "login") {
      if (!apiKey?.trim() || apiKey.startsWith("--")) {
        write(`${name} API 키를 등록하세요: perdix auth login --provider ${provider} --api-key <key>\n키 발급: ${keyUrl}\n`);
        return 1;
      }
      setProviderApiKey(provider, apiKey.trim(), opts.credentialsPath);
      write(`${name} API 키를 ~/.perdix/credentials.json에 저장했습니다.\n`);
      return 0;
    }
    if (action === "logout") {
      const removed = removeProvider(provider, opts.credentialsPath);
      write(removed ? `저장된 ${name} API 키를 지웠습니다.\n` : `저장된 ${name} API 키가 없습니다.\n`);
      return 0;
    }
    if (action === "status") {
      const source = providerStatus(provider, envKey, env, opts.credentialsPath);
      write(source === "env" ? `${provider}: ${envKey} 환경변수 사용 중\n`
        : source === "file" ? `${provider}: ~/.perdix/credentials.json에 저장된 키 사용 중\n`
        : `${provider}: API 키가 등록되지 않았습니다.\n`);
      return 0;
    }
    write(AUTH_USAGE);
    return 1;
  }

  if (provider === "openai") {
    if (args.includes("--api-key") && (apiKey === undefined || apiKey.startsWith("--"))) {
      process.stderr.write("perdix: --api-key 뒤에 API 키를 입력해 주세요.\n");
      return 1;
    }
    if (!["login", "logout", "status"].includes(action)) {
      process.stderr.write(AUTH_USAGE);
      return 1;
    }
    return runOpenAiAuth(action, apiKey, env, opts.credentialsPath, opts.runServerAuth ??
      ((action, env, path) => runServerAuth(action, env, path, opts.signal, opts.onOutput)),
      opts.onOutput ?? ((text) => { process.stdout.write(text); }));
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
    const child = spawn(antPath, forwarded, { stdio: opts.onOutput ? ["inherit", "pipe", "pipe"] : "inherit", env, signal: opts.signal });
    if (opts.onOutput) {
      child.stdout?.setEncoding("utf8").on("data", opts.onOutput);
      child.stderr?.setEncoding("utf8").on("data", opts.onOutput);
    }
    child.once("error", reject);
    child.once("close", (code) => resolvePromise(code ?? 1));
  });
}
