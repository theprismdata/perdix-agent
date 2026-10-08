import { Box, Text, useAnimation } from "ink";
import type { Usage } from "../state.js";

const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

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

function Spinner() {
  const { frame } = useAnimation({ interval: 80 });
  return <Text color="yellow">{SPINNER[frame % SPINNER.length]} </Text>;
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
  return (
    <Box>
      {running && <Spinner />}
      <Text color="magenta">{mode}</Text>
      <Text dimColor> · </Text>
      <Text>{model ?? "default model"}</Text>
      <Text dimColor> · </Text>
      <Text>{formatUsage(usage)}</Text>
      <Text dimColor> · {sessionId.slice(0, 8)}</Text>
      {hint ? <Text color="yellow"> {hint}</Text> : null}
    </Box>
  );
}
