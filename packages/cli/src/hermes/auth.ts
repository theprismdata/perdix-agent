import { createInterface } from "node:readline/promises";
import { Writable } from "node:stream";
import { runAuthCommand } from "../agent/auth.js";

export type AuthProvider = "openai" | "anthropic" | "gemini" | "openrouter";
export type AuthAction = "login" | "logout" | "status";
export interface HermesAuthResult {
  readonly code: number;
  readonly provider?: AuthProvider;
  readonly output?: string;
}

export async function runHermesAuth(
  action: AuthAction, provider: AuthProvider | undefined, signal: AbortSignal,
  server?: string,
): Promise<HermesAuthResult> {
  const authSignal = AbortSignal.any([signal, AbortSignal.timeout(540_000)]);
  if (!provider) {
    const input = createInterface({ input: process.stdin, output: process.stdout });
    const cancelled = new AbortController();
    input.on("SIGINT", () => cancelled.abort());
    try {
      process.stdout.write("Perdix 인증 · 공급자를 선택하세요.\n1. OpenAI (ChatGPT)\n2. Claude (Anthropic)\n3. Gemini (Google API 키)\n4. OpenRouter (API 키)\n0. 취소\n");
      const answer = await input.question("> ", { signal: AbortSignal.any([authSignal, cancelled.signal]) });
      if (answer.trim() === "1") provider = "openai";
      else if (answer.trim() === "2") provider = "anthropic";
      else if (answer.trim() === "3") provider = "gemini";
      else if (answer.trim() === "4") provider = "openrouter";
      else return { code: 130 };
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") return { code: 130 };
      throw error;
    } finally { input.close(); }
  }
  if (action === "login") {
    process.stdout.write(provider === "openai"
      ? "OpenAI 공식 브라우저 인증을 진행합니다. 인증 정보는 로컬 ~/.perdix에 저장합니다.\n"
      : provider === "gemini" ? "Gemini는 Google AI Studio API 키를 사용합니다.\n"
      : provider === "openrouter" ? "OpenRouter는 API 키를 사용합니다.\n"
      : "Anthropic 공식 ant CLI로 브라우저 인증을 진행합니다. 인증 정보는 ant가 로컬에 관리합니다.\n");
  }
  const env = server ? { ...process.env, PERDIX_SERVER: server } : process.env;
  let apiKey: string | undefined;
  if (action === "login" && (provider === "gemini" || provider === "openrouter")) {
    const hidden = new Writable({ write(_chunk, _encoding, callback) { callback(); } });
    const input = createInterface({ input: process.stdin, output: hidden, terminal: true });
    const cancelled = new AbortController();
    input.on("SIGINT", () => cancelled.abort());
    try {
      const name = provider === "openrouter" ? "OpenRouter" : "Gemini";
      const keyUrl = provider === "openrouter" ? "https://openrouter.ai/settings/keys" : "https://aistudio.google.com/apikey";
      process.stdout.write(`키 발급: ${keyUrl}\n${name} API 키 (입력은 표시되지 않습니다): `);
      apiKey = await input.question("", { signal: AbortSignal.any([authSignal, cancelled.signal]) });
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") return { code: 130, provider };
      throw error;
    } finally { input.close(); hidden.destroy(); process.stdout.write("\n"); }
  }
  let output = "";
  try {
    const code = await runAuthCommand([action, "--provider", provider, ...(apiKey !== undefined ? ["--api-key", apiKey] : [])], {
      env, signal: authSignal, ...(action === "status" ? { onOutput: (text: string) => { output += text; } } : {}),
    });
    return { code, provider, output };
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") return { code: 130, provider };
    throw error;
  }
}
