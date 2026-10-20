import { useEffect, useState } from "react";
import { Box, Text } from "ink";
import type { State } from "../state.js";
import { theme } from "../theme.js";

export function requestPhase(state: State, permissionTitle?: string): string {
  if (!state.running) return state.result?.split(/ · |\n/)[0] ?? (state.mode === "general" || state.mode === "plan"
    ? "준비됨 · 읽기 전용 모드 · 변경 작업은 /agent build"
    : "준비됨");
  if (permissionTitle) return `승인 대기 · ${permissionTitle}`;
  const executing = state.live.filter((item) => item.kind === "tool" && item.status === "in_progress");
  const waiting = state.live.filter((item) => item.kind === "tool" && item.status === "pending");
  const active = executing[0];
  if (active?.kind === "tool") return `도구 실행 중 · ${active.title}${executing.length > 1 ? ` 외 ${executing.length - 1}개` : ""}`;
  const pending = waiting[0];
  if (pending?.kind === "tool") return `도구 호출 대기 · ${pending.title}`;
  return "모델 응답 기다리는 중";
}

export function RequestProgress({ state, permissionTitle }: { state: State; permissionTitle?: string }) {
  const [clock, setClock] = useState(() => ({ started: Date.now(), updated: Date.now(), now: Date.now() }));
  useEffect(() => {
    if (!state.running) return;
    const started = Date.now();
    setClock({ started, updated: started, now: started });
    const timer = setInterval(() => setClock((previous) => ({ ...previous, now: Date.now() })), 1000);
    return () => clearInterval(timer);
  }, [state.running]);
  useEffect(() => {
    if (state.running) setClock((previous) => ({ ...previous, updated: Date.now() }));
  }, [state]);
  const elapsed = Math.max(0, Math.floor((clock.now - clock.started) / 1000));
  const quiet = Math.max(0, Math.floor((clock.now - clock.updated) / 1000));
  return (
    <Box flexDirection="column">
      <Text color={state.running ? theme.accent : undefined}>{requestPhase(state, permissionTitle)}</Text>
      {state.running && <Text>경과 {elapsed}초 · Ctrl+C 취소</Text>}
      {state.running && !permissionTitle && quiet >= 30 && <Text color={theme.warning}>새 소식 {quiet}초 없음 · 수신 대기</Text>}
    </Box>
  );
}
