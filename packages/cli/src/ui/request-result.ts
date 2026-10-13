import type { State, TranscriptItem, ToolItem } from "./state.js";

export function currentTurnItems(state: State): TranscriptItem[] {
  return [...state.done, ...state.live].slice(state.turnStartIndex);
}

export function requestResult(state: State, stopReason: string, verification?: string | null): string {
  const items = currentTurnItems(state);
  const tools = items.filter((item): item is ToolItem => item.kind === "tool");
  const unfinished = tools.filter((tool) => tool.status === "pending" || tool.status === "in_progress").length;
  const failed = tools.filter((tool) => tool.status === "failed").length;
  const completed = tools.filter((tool) => tool.status === "completed").length;
  if (stopReason === "cancelled") return "요청이 취소됐습니다.";
  if (stopReason === "error" && state.result) return state.result;
  if (verification === "failed") return "검증에 실패했습니다.\n위 결과를 확인하세요.";
  if (stopReason !== "end_turn") return `처리 미완료 · 종료 사유: ${stopReason}`;
  if (unfinished > 0) return `처리 미완료 · 도구 ${unfinished}개 결과 없음\n다시 요청하세요.`;
  const lastToolOffset = [...items].reverse().findIndex((item) => item.kind === "tool");
  const lastToolIndex = lastToolOffset < 0 ? -1 : items.length - lastToolOffset - 1;
  const hasAnswer = items.slice(lastToolIndex + 1).some((item) => item.kind === "agent" && item.text.trim().length > 0);
  const summary = tools.length > 0 ? ` · 도구 완료 ${completed}개, 실패 ${failed}개` : "";
  const checked = verification === "passed" ? " · 검증 통과" : "";
  return hasAnswer
    ? `응답 완료${summary}${checked}\n위 답변을 확인하세요.`
    : `응답 종료${summary}${checked}\n최종 답변이 없습니다.\n다시 요청하세요.`;
}
