/**
 * `~/.perdix/credentials.json` 읽기/쓰기. 서버 쪽 `config/credentials.py`가 읽는 형식과
 * 그대로 맞아야 한다: `{"<provider>": {"api_key"?, "auth_token"?, "base_url"?}}`.
 * Anthropic은 `ant` CLI(OAuth 프로필)에 위임하므로 이 파일은 그 외 공급자(현재 openai)의
 * API 키 저장용으로만 쓴다.
 */
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export const DEFAULT_CREDENTIALS_PATH = join(homedir(), ".perdix", "credentials.json");

export interface ProviderCredential {
  api_key?: string;
  auth_token?: string;
  base_url?: string;
}

export type CredentialsFile = Record<string, ProviderCredential>;

export function readCredentials(path: string = DEFAULT_CREDENTIALS_PATH): CredentialsFile {
  if (!existsSync(path)) return {};
  try {
    const raw: unknown = JSON.parse(readFileSync(path, "utf8"));
    return raw !== null && typeof raw === "object" ? (raw as CredentialsFile) : {};
  } catch {
    return {};
  }
}

function writeCredentials(data: CredentialsFile, path: string): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(data, null, 2)}\n`, { mode: 0o600 });
  try {
    chmodSync(path, 0o600);
  } catch {
    // Windows 등 POSIX 권한이 없는 환경에서는 best-effort.
  }
}

export function setProviderApiKey(
  provider: string,
  apiKey: string,
  path: string = DEFAULT_CREDENTIALS_PATH,
): void {
  const data = readCredentials(path);
  data[provider] = { ...data[provider], api_key: apiKey };
  writeCredentials(data, path);
}

/** 저장된 항목을 지운다. 지울 게 있었으면 true. */
export function removeProvider(provider: string, path: string = DEFAULT_CREDENTIALS_PATH): boolean {
  const data = readCredentials(path);
  if (!(provider in data)) return false;
  delete data[provider];
  writeCredentials(data, path);
  return true;
}

export type CredentialSource = "env" | "file" | "none";

export function providerStatus(
  provider: string,
  envKey: string,
  env: NodeJS.ProcessEnv,
  path: string = DEFAULT_CREDENTIALS_PATH,
): CredentialSource {
  if (env[envKey]) return "env";
  if (readCredentials(path)[provider]?.api_key) return "file";
  return "none";
}
