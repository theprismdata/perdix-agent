/**
 * CLI 내부 계약: ACP 연결 계층(agent/)과 UI 계층(ui/)이 공유하는 타입.
 * UI는 이 파일만 보고 렌더링하고, agent/는 ACP SessionUpdate를 이 이벤트로 변환한다.
 */
import type { ContentBlock, PermissionOptionKind, ToolKind, StopReason } from "@perdix/protocol";

export type PromptImage = Extract<ContentBlock, { type: "image" }>;

export type SessionMode = "general" | "plan" | "build";

export interface ModeInfo {
  id: string;
  name: string;
  description?: string | null;
}

export interface ModelOption {
  value: string;
  name: string;
  provider?: string;
}

/** 세션 생성/복구 직후 UI가 알아야 하는 정적 정보 */
export interface SessionInfo {
  sessionId: string;
  cwd: string;
  currentMode: string;
  modes: ModeInfo[];
  currentModel: string | null;
  models: ModelOption[];
  assignmentModels?: ModelOption[];
}

export interface DiffContent {
  type: "diff";
  path: string;
  oldText: string | null;
  newText: string;
}
export interface TextContent {
  type: "text";
  text: string;
}
export type ToolContent = DiffContent | TextContent;

/** agent/ → ui/ 로 흐르는 이벤트. 순서는 ACP 수신 순서와 같다. */
export type AgentEvent =
  | { type: "user_text"; text: string } // 리플레이(session/load) 때만
  | { type: "agent_text"; text: string } // 스트리밍 델타
  | { type: "agent_thought"; text: string }
  | {
      type: "tool_call";
      toolCallId: string;
      title: string;
      kind: ToolKind | string;
      status: "pending" | "in_progress" | "completed" | "failed";
      rawInput?: unknown;
      parentToolCallId?: string | null;
    }
  | {
      type: "tool_call_update";
      toolCallId: string;
      status?: "pending" | "in_progress" | "completed" | "failed";
      title?: string;
      content?: ToolContent[];
    }
  | { type: "plan"; entries: { content: string; status: string; priority?: string }[] }
  | { type: "usage"; used: number; size: number; cost?: number | null }
  | { type: "mode_changed"; modeId: string }
  | { type: "model_changed"; model: string }
  | { type: "turn_started" }
  | {
      type: "turn_ended";
      stopReason: StopReason | string;
      verification?: "passed" | "failed" | "skipped" | null;
    }
  | { type: "error"; message: string }
  | { type: "server_exit"; code: number | null };

export interface PermissionRequest {
  requestId: string;
  toolCallId: string;
  title: string;
  kind?: string | null;
  rawInput?: unknown;
  options: { optionId: string; name: string; kind: PermissionOptionKind }[];
}

export type PermissionAnswer = { optionId: string } | { cancelled: true };

/** UI가 호출하는 세션 핸들. agent/AgentSession 이 구현한다. */
export interface SessionHandle {
  readonly info: SessionInfo;
  /** 사용자 프롬프트 1회. 응답이 올 때까지 resolve하지 않는다. */
  prompt(text: string, images?: readonly PromptImage[]): Promise<{ stopReason: string; verification?: string | null }>;
  cancel(): Promise<void>;
  setMode(modeId: string): Promise<void>;
  setModel(model: string): Promise<void>;
  setConfigOption?(configId: string, value: string): Promise<void>;
  /** 이벤트 구독. 반환값은 해제 함수. */
  onEvent(listener: (event: AgentEvent) => void): () => void;
  /** 권한 요청 핸들러 등록. 하나만 등록되며, 없으면 reject_once로 응답한다. */
  onPermission(handler: (req: PermissionRequest) => Promise<PermissionAnswer>): () => void;
  close(): Promise<void>;
}

export interface SessionListItem {
  sessionId: string;
  cwd: string;
  title?: string | null;
  updatedAt?: string | null;
}
