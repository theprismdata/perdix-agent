import { Box, Text } from "ink";
import type { DiffContent, ToolContent } from "../../agent/types.js";
import type { ToolItem, ToolStatus } from "../state.js";
import { theme } from "../theme.js";

export const MAX_DIFF_LINES = 40;
export const MAX_TEXT_LINES = 12;

const STATUS_ICON: Record<ToolStatus, { icon: string; color: string }> = {
  pending: { icon: "○", color: theme.status.pending },
  in_progress: { icon: "◐", color: theme.status.in_progress },
  completed: { icon: "✓", color: theme.status.completed },
  failed: { icon: "✗", color: theme.status.failed },
};

const KIND_ICON: Record<string, string> = {
  read: "≡",
  edit: "✎",
  delete: "⌫",
  move: "⇢",
  search: "⌕",
  execute: "$",
  think: "…",
  fetch: "⇣",
  switch_mode: "⇄",
  other: "•",
};

export interface DiffLine {
  sign: "+" | "-" | " ";
  text: string;
}

function splitLines(text: string): string[] {
  const lines = text.split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/** 공통 앞/뒤 줄을 걷어낸 단순 줄 단위 diff. 편집 도구의 국소 변경 표시에 충분하다. */
export function diffLines(oldText: string | null, newText: string): DiffLine[] {
  const next = splitLines(newText);
  if (oldText === null) return next.map((text) => ({ sign: "+", text }));
  const prev = splitLines(oldText);
  let start = 0;
  while (start < prev.length && start < next.length && prev[start] === next[start]) start++;
  let endPrev = prev.length;
  let endNext = next.length;
  while (endPrev > start && endNext > start && prev[endPrev - 1] === next[endNext - 1]) {
    endPrev--;
    endNext--;
  }
  const out: DiffLine[] = [];
  if (start > 0) out.push({ sign: " ", text: prev[start - 1]! });
  for (const text of prev.slice(start, endPrev)) out.push({ sign: "-", text });
  for (const text of next.slice(start, endNext)) out.push({ sign: "+", text });
  if (endPrev < prev.length) out.push({ sign: " ", text: prev[endPrev]! });
  return out;
}

function DiffView({ diff }: { diff: DiffContent }) {
  const lines = diffLines(diff.oldText, diff.newText);
  const shown = lines.slice(0, MAX_DIFF_LINES);
  const hidden = lines.length - shown.length;
  return (
    <Box flexDirection="column">
      <Text dimColor>{diff.path}</Text>
      {shown.map((line, i) => (
        <Text
          key={i}
          color={line.sign === "+" ? theme.diffAddedFg : line.sign === "-" ? theme.diffRemovedFg : undefined}
          backgroundColor={line.sign === "+" ? theme.diffAddedBg : line.sign === "-" ? theme.diffRemovedBg : undefined}
          dimColor={line.sign === " "}
        >
          {line.sign} {line.text}
        </Text>
      ))}
      {hidden > 0 && <Text dimColor>… {hidden} lines</Text>}
    </Box>
  );
}

function TextView({ text }: { text: string }) {
  const lines = splitLines(text);
  const shown = lines.slice(0, MAX_TEXT_LINES);
  const hidden = lines.length - shown.length;
  return (
    <Box flexDirection="column">
      <Text>{shown.join("\n")}</Text>
      {hidden > 0 && <Text dimColor>… {hidden} lines</Text>}
    </Box>
  );
}

/** 접힌 상태에서 보여줄 한 줄 요약 */
export function summarize(content: ToolContent[]): string {
  if (content.length === 0) return "";
  let added = 0;
  let removed = 0;
  let textLines = 0;
  let diffs = 0;
  for (const c of content) {
    if (c.type === "diff") {
      diffs++;
      for (const l of diffLines(c.oldText, c.newText)) {
        if (l.sign === "+") added++;
        else if (l.sign === "-") removed++;
      }
    } else {
      textLines += splitLines(c.text).length;
    }
  }
  const parts: string[] = [];
  if (diffs > 0) parts.push(`+${added} -${removed}`);
  if (textLines > 0) parts.push(`${textLines} lines`);
  return parts.join(", ");
}

export interface ToolCardProps {
  item: ToolItem;
  expanded: boolean;
}

export function ToolCard({ item, expanded }: ToolCardProps) {
  const status = STATUS_ICON[item.status];
  const kindIcon = KIND_ICON[item.toolKind] ?? KIND_ICON.other;
  const kindColor = theme.toolKind[item.toolKind as keyof typeof theme.toolKind] ?? theme.toolKind.other;
  const summary = summarize(item.content);
  return (
    <Box flexDirection="column" marginLeft={item.depth * 2}>
      <Box>
        <Text color={status.color}>{status.icon} </Text>
        <Text color={kindColor}>
          {kindIcon} {item.toolKind}
        </Text>
        <Text> {item.title}</Text>
        {!expanded && summary !== "" && <Text dimColor> ({summary})</Text>}
      </Box>
      {expanded && item.content.length > 0 && (
        <Box flexDirection="column" marginLeft={2} borderStyle="single" borderLeft borderTop={false} borderRight={false} borderBottom={false} borderColor={theme.border} paddingLeft={1}>
          {item.content.map((c, i) =>
            c.type === "diff" ? <DiffView key={i} diff={c} /> : <TextView key={i} text={c.text} />,
          )}
        </Box>
      )}
    </Box>
  );
}
