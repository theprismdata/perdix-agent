/**
 * perdix-relay 조회. Workflow 편집기의 Perdix Agent 노드 "Model" 칸에 띄울 목록을
 * 중계의 `GET /v1/models`에서, "Instructions" 칸에 보여 줄 역할 원본 시스템 프롬프트를
 * `GET /v1/agents/{role}`에서 받는다. 중계 토큰은 서버가 파일에서 읽고 편집기(웹뷰)에는 넘기지 않는다.
 * 주소는 loopback만 받는다 — 편집기가 넘긴 임의 주소로 토큰을 보내지 않기 위해서다.
 */
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export const DEFAULT_RELAY_TOKEN_FILE = join(homedir(), ".perdix", "relay-token");
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);

export class RelayRequestError extends Error {}

/** loopback http(s) 주소면 끝 슬래시를 뗀 원점을, 아니면 undefined. */
export function loopbackRelayBase(raw: string): string | undefined {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return undefined;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return undefined;
  if (!LOOPBACK_HOSTS.has(url.hostname)) return undefined;
  return url.origin;
}

async function relayGet(relayUrl: string, path: string, tokenFile: string): Promise<unknown> {
  const base = loopbackRelayBase(relayUrl);
  if (base === undefined) throw new RelayRequestError("중계 주소는 127.0.0.1/localhost만 쓸 수 있습니다");
  let token: string;
  try {
    token = readFileSync(tokenFile, "utf8").trim();
  } catch {
    throw new RelayRequestError(`중계 토큰 파일을 읽지 못했습니다: ${tokenFile}`);
  }
  const response = await fetch(`${base}${path}`, {
    headers: { authorization: `Bearer ${token}` },
    signal: AbortSignal.timeout(10_000),
  }).catch((error: unknown) => {
    throw new RelayRequestError(`중계에 연결하지 못했습니다(${base}): ${error instanceof Error ? error.message : String(error)}`);
  });
  if (!response.ok) throw new RelayRequestError(`중계 ${response.status}`);
  return response.json();
}

export async function listRelayModels(relayUrl: string, tokenFile = DEFAULT_RELAY_TOKEN_FILE): Promise<string[]> {
  const body = await relayGet(relayUrl, "/v1/models", tokenFile) as { data?: Array<{ id?: unknown }> };
  return (body.data ?? []).map((item) => item.id).filter((id): id is string => typeof id === "string");
}

/** 역할의 원본 시스템 프롬프트. 워크플로 전용 지시문을 고쳐 쓰기 위한 출발점이다. */
export async function getRelayRolePrompt(relayUrl: string, role: string, tokenFile = DEFAULT_RELAY_TOKEN_FILE): Promise<string> {
  if (!/^[A-Za-z0-9_-]+$/.test(role)) throw new RelayRequestError(`알 수 없는 역할 이름: ${role}`);
  const body = await relayGet(relayUrl, `/v1/agents/${role}`, tokenFile) as { systemPrompt?: unknown };
  if (typeof body.systemPrompt !== "string") throw new RelayRequestError("중계 응답에 systemPrompt가 없습니다");
  return body.systemPrompt;
}
