import { Box, Static, Text } from "ink";
import type { TranscriptItem } from "../state.js";
import { theme } from "../theme.js";
import { ToolCard } from "./ToolCard.js";

const PLAN_STYLE = {
  completed: { glyph: "☑", color: theme.success },
  in_progress: { glyph: "▶", color: theme.warning },
  pending: { glyph: "☐", color: theme.muted },
} as const;

function ItemView({ item, expanded }: { item: TranscriptItem; expanded: boolean }) {
  switch (item.kind) {
    case "user":
      return (
        <Box marginTop={1}>
          <Text color={theme.userPrefix} bold>
            {"› "}
          </Text>
          <Text>{item.text}</Text>
        </Box>
      );
    case "agent":
      return (
        <Box borderStyle="single" borderLeft borderTop={false} borderRight={false} borderBottom={false} borderColor={theme.dim} paddingLeft={1}>
          <Text>{item.text}</Text>
        </Box>
      );
    case "thought":
      return (
        <Text dimColor italic>
          {item.text}
        </Text>
      );
    case "notice":
      return <Text color={theme.muted}>{item.text}</Text>;
    case "error":
      return <Text color={theme.error}>{item.text}</Text>;
    case "plan":
      return (
        <Box flexDirection="column">
          <Text bold>Plan</Text>
          {item.entries.map((e, i) => {
            const style = e.status === "completed" || e.status === "in_progress" ? PLAN_STYLE[e.status] : PLAN_STYLE.pending;
            return (
              <Text key={i}>
                <Text color={style.color}>{style.glyph}</Text>
                <Text color={e.status === "pending" ? theme.muted : undefined} dimColor={e.status === "completed"}>
                  {" "}
                  {e.content}
                </Text>
              </Text>
            );
          })}
        </Box>
      );
    case "tool":
      return <ToolCard item={item} expanded={expanded} />;
  }
}

export interface TranscriptProps {
  done: TranscriptItem[];
  live: TranscriptItem[];
  /** live 도구 카드를 모두 펼칠지(Ctrl+O). 기본은 가장 최근 도구 카드만 펼친다. */
  expandAll: boolean;
}

/** 완료 항목은 <Static>으로 한 번만 출력하고, 진행 중 항목만 동적 영역에 그린다. */
export function Transcript({ done, live, expandAll }: TranscriptProps) {
  let lastTool = -1;
  live.forEach((item, i) => {
    if (item.kind === "tool") lastTool = i;
  });
  return (
    <>
      <Static items={done}>
        {(item) => (
          <Box key={item.id} flexDirection="column">
            <ItemView item={item} expanded={item.kind === "tool" && item.expanded} />
          </Box>
        )}
      </Static>
      <Box flexDirection="column">
        {live.map((item, i) => (
          <ItemView key={item.id} item={item} expanded={expandAll || i === lastTool} />
        ))}
      </Box>
    </>
  );
}
