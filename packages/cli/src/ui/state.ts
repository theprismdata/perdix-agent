/**
 * TUI 트랜스크립트 상태와 순수 리듀서. Ink 없이 테스트할 수 있도록 렌더링과 분리한다.
 *
 * 항목은 두 구역으로 나뉜다.
 * - done: 더 이상 바뀌지 않는 항목. `<Static>`으로 한 번만 출력된다.
 * - live: 진행 중인 항목. 동적 영역에 다시 그려진다.
 * live 앞쪽부터 "정착된" 항목만 done으로 옮기므로 화면 순서는 수신 순서와 같다.
 */
import type { AgentEvent, SessionInfo, ToolContent } from "../agent/types.js";
import { requestResult } from "./request-result.js";

export type ToolStatus = "pending" | "in_progress" | "completed" | "failed";

export interface TextItem {
  kind: "user" | "agent" | "thought" | "notice" | "error";
  id: string;
  text: string;
}

export interface ToolItem {
  kind: "tool";
  id: string;
  toolCallId: string;
  title: string;
  toolKind: string;
  status: ToolStatus;
  rawInput?: unknown;
  parentToolCallId: string | null;
  /** 중첩 깊이(parentToolCallId 체인 길이). 들여쓰기에 쓴다. */
  depth: number;
  content: ToolContent[];
  /** done으로 옮길 때 정해진다. live에서는 렌더러가 결정한다. */
  expanded: boolean;
  endedWithoutResult?: boolean;
}

export interface PlanItem {
  kind: "plan";
  id: string;
  entries: { content: string; status: string; priority?: string }[];
}

export type TranscriptItem = TextItem | ToolItem | PlanItem;

export interface Usage {
  used: number;
  size: number;
  cost?: number | null;
}

export interface State {
  done: TranscriptItem[];
  live: TranscriptItem[];
  running: boolean;
  turnStartIndex: number;
  result: string | null;
  mode: string;
  model: string | null;
  usage: Usage | null;
  sessionId: string;
  serverExited: boolean;
  /** 항목 id 발급용 카운터 */
  seq: number;
}

export function initialState(info: Pick<SessionInfo, "sessionId" | "currentMode" | "currentModel">): State {
  return {
    done: [],
    live: [],
    running: false,
    turnStartIndex: 0,
    result: null,
    mode: info.currentMode,
    model: info.currentModel,
    usage: null,
    sessionId: info.sessionId,
    serverExited: false,
    seq: 0,
  };
}

function isTerminal(status: ToolStatus): boolean {
  return status === "completed" || status === "failed";
}

function nextId(state: State): [string, number] {
  return [`i${state.seq}`, state.seq + 1];
}

/** live 앞쪽에서 정착된 항목을 done으로 옮긴다. all이면 live 전체를 옮긴다(턴 종료). */
function settle(state: State, all: boolean): State {
  const live = state.live;
  let cut = 0;
  if (all) {
    cut = live.length;
  } else {
    while (cut < live.length) {
      const item = live[cut]!;
      const later = live.slice(cut + 1);
      if (item.kind === "tool") {
        // 완료됐고 뒤에 다른 도구가 있을 때만 정착: 최근 도구 카드는 펼친 채 live에 남긴다.
        if (!isTerminal(item.status) || !later.some((x) => x.kind === "tool")) break;
      } else if (later.length === 0) {
        // 텍스트/계획은 뒤에 다른 항목이 와야 끝난 것으로 본다(스트리밍 델타 누적).
        break;
      }
      cut++;
    }
  }
  if (cut === 0) return state;
  const moved = live.slice(0, cut);
  const remaining = live.slice(cut);
  let lastToolIdx = -1;
  if (all) {
    for (let i = moved.length - 1; i >= 0; i--) {
      if (moved[i]!.kind === "tool") {
        lastToolIdx = i;
        break;
      }
    }
  }
  const frozen = moved.map((item, i) =>
    item.kind === "tool" ? { ...item, expanded: i === lastToolIdx || item.status === "failed", endedWithoutResult: all && !isTerminal(item.status) } : item,
  );
  return { ...state, done: [...state.done, ...frozen], live: remaining };
}

function pushItem(state: State, make: (id: string) => TranscriptItem): State {
  const [id, seq] = nextId(state);
  return { ...state, seq, live: [...state.live, make(id)] };
}

/** 같은 종류의 텍스트가 live 끝에 있으면 이어 붙이고, 아니면 새 항목을 만든다. */
function appendText(state: State, kind: TextItem["kind"], text: string): State {
  const last = state.live[state.live.length - 1];
  if (last && last.kind === kind) {
    const merged: TextItem = { ...last, text: last.text + text };
    return { ...state, live: [...state.live.slice(0, -1), merged] };
  }
  return pushItem(state, (id) => ({ kind, id, text }));
}

function findToolIndex(state: State, toolCallId: string): number {
  return state.live.findIndex((x) => x.kind === "tool" && x.toolCallId === toolCallId);
}

function depthOf(state: State, parent: string | null | undefined): number {
  if (!parent) return 0;
  const all = [...state.done, ...state.live];
  const p = all.find((x): x is ToolItem => x.kind === "tool" && x.toolCallId === parent);
  return p ? p.depth + 1 : 1;
}

function applyEvent(state: State, event: AgentEvent): State {
  switch (event.type) {
    case "user_text":
      return pushItem(state, (id) => ({ kind: "user", id, text: event.text }));
    case "agent_text":
      return appendText(state, "agent", event.text);
    case "agent_thought":
      return appendText(state, "thought", event.text);
    case "tool_call": {
      const idx = findToolIndex(state, event.toolCallId);
      if (idx >= 0) {
        const cur = state.live[idx] as ToolItem;
        const updated: ToolItem = {
          ...cur,
          title: event.title,
          toolKind: String(event.kind),
          status: event.status,
          rawInput: event.rawInput ?? cur.rawInput,
        };
        const live = [...state.live];
        live[idx] = updated;
        return { ...state, live };
      }
      const parent = event.parentToolCallId ?? null;
      const depth = depthOf(state, parent);
      return pushItem(state, (id) => ({
        kind: "tool",
        id,
        toolCallId: event.toolCallId,
        title: event.title,
        toolKind: String(event.kind),
        status: event.status,
        rawInput: event.rawInput,
        parentToolCallId: parent,
        depth,
        content: [],
        expanded: false,
      }));
    }
    case "tool_call_update": {
      const idx = findToolIndex(state, event.toolCallId);
      // 이미 Static으로 나간 카드나 모르는 id는 바꿀 수 없으므로 무시한다.
      if (idx < 0) return state;
      const cur = state.live[idx] as ToolItem;
      const updated: ToolItem = {
        ...cur,
        status: event.status ?? cur.status,
        title: event.title ?? cur.title,
        content: event.content ?? cur.content,
      };
      const live = [...state.live];
      live[idx] = updated;
      return { ...state, live };
    }
    case "plan": {
      const last = state.live[state.live.length - 1];
      if (last && last.kind === "plan") {
        return { ...state, live: [...state.live.slice(0, -1), { ...last, entries: event.entries }] };
      }
      return pushItem(state, (id) => ({ kind: "plan", id, entries: event.entries }));
    }
    case "usage":
      return { ...state, usage: { used: event.used, size: event.size, cost: event.cost ?? null } };
    case "mode_changed":
      return { ...state, mode: event.modeId };
    case "model_changed":
      return { ...state, model: event.model };
    case "turn_started":
      return { ...state, running: true, result: null, turnStartIndex: state.done.length + state.live.length };
    case "turn_ended": {
      const result = requestResult(state, event.stopReason, event.verification);
      const next = pushItem({ ...state, running: false, result }, (id) => ({ kind: "notice", id, text: result }));
      return settle(next, true);
    }
    case "error":
      return pushItem({ ...state, result: `처리 오류 · ${event.message}` }, (id) => ({ kind: "error", id, text: event.message }));
    case "server_exit": {
      const next = pushItem(
        { ...state, running: false, serverExited: true, result: "서버 연결 종료 · Perdix를 다시 실행하세요." },
        (id) => ({ kind: "error", id, text: `server exited (code ${event.code ?? "null"})` }),
      );
      return settle(next, true);
    }
  }
}

/** AgentEvent 하나를 상태에 반영한다. */
export function reduce(state: State, event: AgentEvent): State {
  const next = applyEvent(state, event);
  if (next === state) return state;
  if (event.type === "turn_ended" || event.type === "server_exit") return next;
  return settle(next, false);
}

/** UI 로컬 액션: 안내 문구, 턴 강제 종료(prompt 실패 시). */
export type UiAction =
  | AgentEvent
  | { type: "ui_notice"; text: string; error?: boolean }
  | { type: "ui_idle" };

export function reduceUi(state: State, action: UiAction): State {
  switch (action.type) {
    case "ui_notice":
      return settle(
        pushItem(state, (id) => ({ kind: action.error ? "error" : "notice", id, text: action.text })),
        !state.running,
      );
    case "ui_idle":
      if (!state.running) return state;
      return settle(pushItem(
        { ...state, running: false, result: state.result ?? "처리 미완료 · 종료 결과를 받지 못했습니다. 다시 요청하세요." },
        (id) => ({ kind: "notice", id, text: state.result ?? "처리 미완료 · 종료 결과를 받지 못했습니다. 다시 요청하세요." }),
      ), true);
    default:
      return reduce(state, action);
  }
}
