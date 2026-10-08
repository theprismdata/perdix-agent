import { Box, Text, useAnimation, useWindowSize } from "ink";
import type { Usage } from "../state.js";
import { theme } from "../theme.js";

const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
/** 스피너 글자 + 뒤 공백 */
const SPINNER_WIDTH = 2;
const SEP = " · ";

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function formatUsage(usage: Usage | null): string {
  if (!usage) return "ctx -";
  const pct = usage.size > 0 ? Math.round((usage.used / usage.size) * 100) : 0;
  return `ctx ${formatTokens(usage.used)}/${formatTokens(usage.size)} (${pct}%)`;
}

/** `[█████░░░░░]` 스타일 채움 막대. usage가 없거나 size<=0이면 빈 문자열. */
export function contextBar(usage: Usage | null, slots = 10): string {
  if (!usage || usage.size <= 0) return "";
  const pct = Math.max(0, Math.min(1, usage.used / usage.size));
  const filled = Math.round(pct * slots);
  return `[${"█".repeat(filled)}${"░".repeat(slots - filled)}]`;
}

/** 컨텍스트 사용률에 따른 막대 색. <60% 성공, 60–85% 경고, >85% 오류. */
export function contextBarColor(usage: Usage | null): string {
  if (!usage || usage.size <= 0) return theme.success;
  const pct = usage.used / usage.size;
  if (pct > 0.85) return theme.error;
  if (pct >= 0.6) return theme.warning;
  return theme.success;
}

export interface StatusSegment {
  /** 본문 앞에 흐리게 그리는 구분자(예: " · "). 폭 계산에 포함된다. */
  separator?: string;
  text: string;
  color?: string;
  dimColor?: boolean;
}

export function segmentWidth(seg: StatusSegment): number {
  return (seg.separator ?? "").length + seg.text.length;
}

/**
 * `pinned`은 width와 무관하게 항상 포함한다. `tail`은 우선순위가 높은 것부터 넣되,
 * 남은 폭을 넘는 segment는 통째로 건너뛰고(중간에 자르지 않음) 뒤의 더 짧은 segment는
 * 계속 시도한다. 반환값은 pinned + 들어간 tail의 순서.
 */
export function layoutStatusBar(pinned: StatusSegment[], tail: StatusSegment[], width: number): StatusSegment[] {
  let budget = width - pinned.reduce((sum, s) => sum + segmentWidth(s), 0);
  const included: StatusSegment[] = [];
  for (const seg of tail) {
    const w = segmentWidth(seg);
    if (w <= budget) {
      included.push(seg);
      budget -= w;
    }
  }
  return [...pinned, ...included];
}

function Spinner() {
  const { frame } = useAnimation({ interval: 80 });
  return <Text color={theme.accentStrong}>{SPINNER[frame % SPINNER.length]} </Text>;
}

export interface StatusBarProps {
  mode: string;
  model: string | null;
  usage: Usage | null;
  sessionId: string;
  running: boolean;
  /** 우측에 잠깐 띄우는 안내(예: "Ctrl+C again to exit") */
  hint?: string | null;
}

export function StatusBar({ mode, model, usage, sessionId, running, hint }: StatusBarProps) {
  const { columns } = useWindowSize();
  const pinned: StatusSegment[] = [
    { text: mode, color: theme.accent },
    { separator: SEP, text: model ?? "default model" },
  ];
  const bar = contextBar(usage);
  // 우선순위 높은 순. 좁으면 뒤쪽(덜 중요한) segment부터 통째로 빠진다.
  const tail: StatusSegment[] = [
    { separator: SEP, text: formatUsage(usage) },
    ...(bar ? [{ text: ` ${bar}`, color: contextBarColor(usage) }] : []),
    { separator: SEP, text: sessionId.slice(0, 8), dimColor: true },
  ];
  // hint는 드물고 중요한 안내라 예산 밖에서 항상 그리되, 그 폭만큼 tail 예산을 줄인다.
  const hintText = hint ? ` ${hint}` : "";
  const width = columns - (running ? SPINNER_WIDTH : 0) - hintText.length;
  const segments = layoutStatusBar(pinned, tail, width);
  return (
    <Box>
      {running && <Spinner />}
      {segments.map((seg, i) => (
        <Text key={i}>
          {seg.separator ? <Text dimColor>{seg.separator}</Text> : null}
          <Text color={seg.color} dimColor={seg.dimColor}>
            {seg.text}
          </Text>
        </Text>
      ))}
      {hintText ? <Text color={theme.warning}>{hintText}</Text> : null}
    </Box>
  );
}
