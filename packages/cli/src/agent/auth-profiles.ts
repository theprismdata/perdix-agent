import { spawn } from "node:child_process";
import { z } from "zod";
import { resolveServerCommand } from "./spawn.js";

const profileCommand = z.object({
  action: z.enum(["login", "logout", "status", "list", "use"]),
  provider: z.enum(["anthropic", "openai", "gemini", "openrouter"]),
  profile: z.string().regex(/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/).optional(),
  apiKey: z.string().trim().min(1).optional(),
  credentialsPath: z.string().min(1).optional(),
});

export interface ProfileCommandOptions {
  readonly env: NodeJS.ProcessEnv;
  readonly signal?: AbortSignal;
  readonly credentialsPath?: string;
  readonly onOutput?: (text: string) => void;
}

export async function runProfileAuth(args: readonly string[], opts: ProfileCommandOptions): Promise<number> {
  const input: Record<string, string> = { action: args[0] ?? "status", provider: "anthropic" };
  for (let index = 1; index < args.length; index++) {
    const flag = args[index];
    const value = args[++index];
    if (!value || value.startsWith("--")) {
      opts.onOutput?.("인증 옵션 뒤에 값을 입력하세요.\n");
      return 1;
    }
    switch (flag) {
      case "--provider": input.provider = value; break;
      case "--profile": input.profile = value; break;
      case "--api-key": input.apiKey = value; break;
      case "--credentials": input.credentialsPath = value; break;
      default:
        opts.onOutput?.("지원하지 않는 인증 옵션입니다.\n");
        return 1;
    }
  }
  const parsed = profileCommand.safeParse(input);
  if (!parsed.success) {
    opts.onOutput?.("인증 명령·제공자·프로필 이름을 확인하세요.\n");
    return 1;
  }
  const command = parsed.data;
  const server = resolveServerCommand({ env: opts.env });
  const argv = [...server.args, "--auth-command", command.action, "--auth-provider", command.provider];
  if (command.profile) argv.push("--auth-profile", command.profile);
  const credentialsPath = opts.credentialsPath ?? command.credentialsPath;
  if (credentialsPath) argv.push("--credentials", credentialsPath);
  if (command.apiKey !== undefined) argv.push("--auth-api-key-stdin");
  return await new Promise<number>((resolveExit, reject) => {
    const child = spawn(server.command, argv, {
      env: opts.env, signal: opts.signal, stdio: ["pipe", "pipe", "pipe"],
    });
    const write = opts.onOutput ?? ((text: string) => { process.stdout.write(text); });
    child.stdout.setEncoding("utf8").on("data", write);
    child.stderr.setEncoding("utf8").on("data", write);
    child.stdin.on("error", (error: NodeJS.ErrnoException) => {
      if (error.code !== "EPIPE") reject(error);
    });
    child.stdin.end(command.apiKey ?? "");
    child.once("error", reject);
    child.once("close", code => resolveExit(code ?? 1));
  });
}
