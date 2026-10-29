/**
 * Hermes 앱이 화면을 그리며 묻는 요청 중 Perdix가 답할 수 있는 것들. 응답 모양은 Hermes의
 * `gateway-contract.generated.ts`를 따른다. Perdix에 없는 기능(마스코트, 음성 호출어, 커넥터,
 * 무료 이용 안내, 사용량 통계)은 오류가 아니라 "없음"을 뜻하는 정상 응답으로 답하고, 있는 기능
 * (MCP 서버, 프로젝트, 준비 상태, 프로필)은 실제 값으로 답한다.
 */
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { basename, join } from "node:path";
import { z } from "zod";
import type { SessionListItem } from "../agent/types.js";

type Json = Record<string, unknown>;

export const DEFAULT_MCP_CONFIG = join(homedir(), ".perdix", "mcp.json");

/** Perdix에 해당 기능이 없어 "없음"으로 답하는 요청. */
const ABSENT: Readonly<Record<string, () => Json>> = {
  ping: () => ({ pong: true }),
  "pet.info": () => ({ enabled: false }),
  // Perdix는 사용 통계를 보내지 않는다. 사용자가 이미 정한 것으로 답해 동의 창이 뜨지 않게 한다.
  "shared_metrics.status": () => ({ enabled: false, send: false, decided: true, reask: false }),
  "free_tier.status": () => ({
    has_guest: false, enabled: false, available: false, notice_pending: false, model: "", label: "",
  }),
  "wake.status": () => ({
    listening: false, owned_by_caller: false, phrase: "", provider: "none", configured_surface: "",
    input_device: {}, available: false, hint: "Perdix는 음성 호출어를 지원하지 않습니다.", enabled: false,
    audio_silent: true, capture: "none", local_input_available: false, sample_rate: 0, frame_length: 0,
  }),
  "connectors.list": () => ({ available: false, connectors: [] }),
  "connectors.catalog": () => ({ connectors: [] }),
  "connectors.accounts": () => ({ accounts: [] }),
  "connectors.policy.get": () => ({
    layers: [],
    effective: { version: 1, revision: "perdix", issued_at_ms: Date.now(), mode: "unrestricted" },
  }),
  // Hermes 계정의 결제·구독 상태. Perdix에는 계정이 없어 로그인하지 않은 상태로 답한다.
  "billing.state": () => ({ ok: true, logged_in: false }),
  "subscription.state": () => ({ ok: true, logged_in: false }),
  "client.capabilities": () => ({ server_requests: ["approval"] }),
  "i18n.languages": () => ({
    languages: [
      { id: "en", endonym: "English", rtl: false, source: "builtin" },
      { id: "ko", endonym: "한국어", rtl: false, source: "builtin" },
    ],
  }),
};

export function absentFeature(method: string): Json | undefined {
  return ABSENT[method]?.();
}

const mcpFile = z.object({
  mcpServers: z.record(z.string(), z.object({
    command: z.string().optional(),
    args: z.array(z.string()).default([]),
    env: z.record(z.string(), z.string()).default({}),
    enabled: z.boolean().default(true),
  }).passthrough()).default({}),
}).passthrough();

interface McpEntry { name: string; command: string | null; args: string[]; env: string[]; enabled: boolean }

function readMcp(path: string): McpEntry[] {
  try {
    const parsed = mcpFile.parse(JSON.parse(readFileSync(path, "utf8")));
    return Object.entries(parsed.mcpServers).map(([name, server]) => ({
      name, command: server.command ?? null, args: server.args,
      // 환경변수는 값이 비밀일 수 있어 이름만 보낸다.
      env: Object.keys(server.env), enabled: server.enabled,
    }));
  } catch {
    return [];
  }
}

/** 전역 `~/.perdix/mcp.json`의 MCP 서버. 프로젝트 `.mcp.json`은 신뢰 확인이 필요해 여기서는 읽지 않는다. */
export function mcpServersList(path = DEFAULT_MCP_CONFIG): Json {
  return {
    servers: readMcp(path).map((server) => ({
      name: server.name, transport: "stdio", url: null, command: server.command, args: server.args,
      env: server.env, auth: null, oauth_tokens_present: null, enabled: server.enabled, tools: null,
      source: "config", plugin: null,
    })),
  };
}

/** 실제 연결 상태는 Perdix 서버 안에 있어 여기서는 알 수 없다. 설정만 확인한 상태(`configured`)로 답한다. */
export function mcpServersStatus(path = DEFAULT_MCP_CONFIG): Json {
  return {
    servers: readMcp(path).map((server) => ({
      name: server.name, transport: "stdio", tools: 0, connected: false, disabled: !server.enabled,
      status: server.enabled ? "configured" : "disabled", source: "config", plugin: null,
    })),
    checked_at: Date.now() / 1000,
  };
}

/** 저장된 세션을 작업 폴더별 프로젝트로 묶는다. */
export function projectsTree(sessions: readonly SessionListItem[], activeCwd?: string): Json {
  const byFolder = new Map<string, SessionListItem[]>();
  for (const session of sessions) byFolder.set(session.cwd, [...(byFolder.get(session.cwd) ?? []), session]);
  // 앱은 `repos`와 그 안의 `groups`가 항상 있다고 가정하고 순회한다(타입 정의에는 선택 항목이다).
  // Perdix 세션에는 저장소·브랜치 구분이 없어, 작업 폴더 하나를 저장소 하나, 기본 줄기 하나로 둔다.
  const projects = [...byFolder].map(([cwd, items]) => ({
    id: cwd, label: basename(cwd) || cwd, path: cwd, isAuto: true, sessionCount: items.length,
    lastActive: Math.max(0, ...items.map((item) => (item.updatedAt ? Date.parse(item.updatedAt) / 1000 : 0))),
    totalTokens: 0, totalCostUsd: 0,
    repos: [{
      id: cwd, label: basename(cwd) || cwd, path: cwd, sessionCount: items.length,
      groups: [{ id: "main", label: "main", path: cwd, isMain: true, sessions: [] }],
    }],
    previewSessions: [],
    sessionIds: items.map((item) => item.sessionId),
  }));
  return { projects, active_id: activeCwd && byFolder.has(activeCwd) ? activeCwd : null };
}

export interface RuntimeInfo {
  readonly model?: string | null;
  readonly provider?: string | null;
}

/** 지금 Perdix 서버가 모델을 쓸 준비가 됐는지(세션에 현재 모델이 있는지). */
export function runtimeCheck(info: RuntimeInfo | null): Json {
  if (info?.model) return { ok: true, provider: info.provider ?? null, model: info.model, source: "perdix" };
  return { ok: false, provider: null, model: null, source: "perdix", error: "Perdix에 선택된 모델이 없습니다. 인증과 모델 설정을 확인하세요." };
}

/** Perdix에는 프로필이 없어 하나의 기본 프로필로 답한다. */
export function profilesList(info: RuntimeInfo | null, path: string): Json {
  return {
    profiles: [{
      name: "default", path, is_default: true, model: info?.model ?? null, provider: info?.provider ?? null,
      display_name: "Perdix", description: "Perdix 기본 설정", skill_count: 0,
    }],
    bot_mode_protocol: false,
  };
}
