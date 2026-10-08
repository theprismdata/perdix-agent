import { useState } from "react";
import { Box, Text, useInput } from "ink";
import type { PermissionAnswer, PermissionRequest } from "../../agent/types.js";

/** 단축키 → 옵션 kind. 대문자 N은 "항상 거부". */
const KEY_TO_KIND: Record<string, string> = {
  y: "allow_once",
  a: "allow_always",
  n: "reject_once",
  N: "reject_always",
};

const KIND_TO_KEY: Record<string, string> = Object.fromEntries(
  Object.entries(KEY_TO_KIND).map(([k, v]) => [v, k]),
);

export interface PermissionPromptProps {
  request: PermissionRequest;
  onAnswer: (answer: PermissionAnswer) => void;
}

export function PermissionPrompt({ request, onAnswer }: PermissionPromptProps) {
  const [selected, setSelected] = useState(0);
  const options = request.options;

  useInput((input, key) => {
    if (key.ctrl) return; // Ctrl+C 등은 App이 처리한다.
    if (key.upArrow) {
      setSelected((s) => (s - 1 + options.length) % options.length);
      return;
    }
    if (key.downArrow) {
      setSelected((s) => (s + 1) % options.length);
      return;
    }
    if (key.return) {
      const opt = options[selected];
      if (opt) onAnswer({ optionId: opt.optionId });
      return;
    }
    if (key.escape) {
      const reject = options.find((o) => o.kind === "reject_once");
      onAnswer(reject ? { optionId: reject.optionId } : { cancelled: true });
      return;
    }
    const kind = KEY_TO_KIND[input];
    if (kind) {
      const opt = options.find((o) => o.kind === kind);
      if (opt) onAnswer({ optionId: opt.optionId });
      return;
    }
    const n = Number.parseInt(input, 10);
    if (Number.isInteger(n) && n >= 1 && n <= options.length) {
      onAnswer({ optionId: options[n - 1]!.optionId });
    }
  });

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="yellow" paddingX={1}>
      <Text bold color="yellow">
        Permission required
      </Text>
      <Text>
        {request.kind ? `[${request.kind}] ` : ""}
        {request.title}
      </Text>
      {options.map((opt, i) => {
        const shortcut = KIND_TO_KEY[opt.kind];
        const active = i === selected;
        return (
          <Text key={opt.optionId} color={active ? "cyan" : undefined}>
            {active ? "❯ " : "  "}
            {i + 1}. {opt.name}
            {shortcut ? ` (${shortcut})` : ""}
          </Text>
        );
      })}
      <Text dimColor>y/a/n/N or 1-{options.length}, ↑/↓ + Enter, Esc rejects</Text>
    </Box>
  );
}
