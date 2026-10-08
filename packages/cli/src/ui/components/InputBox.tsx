import { useEffect, useRef, useState } from "react";
import { Box, Text, useInput } from "ink";
import { theme } from "../theme.js";

export interface SlashCommandHint {
  name: string;
  hint: string;
  /** `/agent `, `/model ` 처럼 명령 뒤 인자 후보. 없으면 인자 자동완성을 하지 않는다. */
  args?: string[];
}

interface Suggestion {
  kind: "command" | "arg";
  /** 팝업에 보여줄 표시용 문자열 */
  label: string;
  /** Tab/Enter로 확정할 때 입력창에 채워 넣을 전체 값. command는 뒤에 공백 포함, arg는 공백 없음(마지막 토큰이라 바로 제출 가능해야 하므로). */
  complete: string;
}

/** 첫 토큰이면 명령 이름, 두 번째 토큰이면 그 명령의 인자 후보를 만든다. */
function buildSuggestions(current: string, cmds: SlashCommandHint[]): Suggestion[] {
  if (current.includes("\n")) return [];
  const tokens = current.split(" ");
  if (tokens.length === 1) {
    const token = tokens[0] ?? "";
    if (!token.startsWith("/") || token.length === 0) return [];
    return cmds
      .filter((c) => c.name.startsWith(token))
      .map((c) => ({ kind: "command" as const, label: `${c.name} ${c.hint}`, complete: `${c.name} ` }));
  }
  if (tokens.length === 2) {
    const [cmdToken = "", argToken = ""] = tokens;
    const cmd = cmds.find((c) => c.name === cmdToken);
    if (!cmd?.args) return [];
    return cmd.args
      .filter((a) => a.startsWith(argToken))
      .map((a) => ({ kind: "arg" as const, label: a, complete: `${cmdToken} ${a}` }));
  }
  return [];
}

export interface InputBoxProps {
  /** false면 키 입력을 받지 않는다(권한 프롬프트 표시 중). */
  active: boolean;
  onSubmit: (text: string) => void;
  placeholder?: string;
  /** `/`로 시작하는 입력 중 보여줄 자동완성 후보. */
  commands?: SlashCommandHint[];
}

/**
 * 한 줄 입력창. 줄 끝이 `\`이면 Enter가 줄바꿈으로 바뀐다(Shift+Enter 대체).
 * ↑/↓ 로 이전 제출 기록을 순회한다. `/` 명령 입력 중에는 후보 팝업이 뜨고
 * ↑/↓ 로 선택, Tab/Enter 로 완성한다.
 */
export function InputBox({ active, onSubmit, placeholder, commands = [] }: InputBoxProps) {
  const [value, setValue] = useState("");
  const history = useRef<string[]>([]);
  // history.current.length 는 "새 입력" 위치를 뜻한다.
  const [cursor, setCursor] = useState(0);
  const draft = useRef("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const suggestions = buildSuggestions(value, commands);
  const showPopup = active && suggestions.length > 0;
  const activeIndex = suggestions.length > 0 ? Math.min(selectedIndex, suggestions.length - 1) : 0;

  // 입력이 바뀌면 강조 후보를 맨 위로 되돌린다.
  useEffect(() => setSelectedIndex(0), [value]);

  useInput(
    (input, key) => {
      if (key.ctrl || key.meta || key.escape) return;
      if (key.return) {
        if (value.endsWith("\\")) {
          setValue(value.slice(0, -1) + "\n");
          return;
        }
        if (showPopup) {
          const exact = suggestions.find((s) =>
            s.kind === "command" ? s.complete.trim() === value : s.complete === value,
          );
          if (!exact) {
            const chosen = suggestions[activeIndex];
            if (chosen) {
              setValue(chosen.complete);
              return;
            }
          }
        }
        const text = value.trim();
        if (text === "") return;
        history.current.push(value);
        setCursor(history.current.length);
        draft.current = "";
        setValue("");
        onSubmit(value);
        return;
      }
      if (key.tab) {
        if (showPopup) {
          const chosen = suggestions[activeIndex];
          if (chosen) setValue(chosen.complete);
        }
        return;
      }
      if (key.upArrow) {
        if (showPopup) {
          setSelectedIndex((i) => (i - 1 + suggestions.length) % suggestions.length);
          return;
        }
        if (cursor === 0) return;
        if (cursor === history.current.length) draft.current = value;
        setCursor(cursor - 1);
        setValue(history.current[cursor - 1] ?? "");
        return;
      }
      if (key.downArrow) {
        if (showPopup) {
          setSelectedIndex((i) => (i + 1) % suggestions.length);
          return;
        }
        if (cursor >= history.current.length) return;
        const next = cursor + 1;
        setCursor(next);
        setValue(next === history.current.length ? draft.current : (history.current[next] ?? ""));
        return;
      }
      if (key.backspace || key.delete) {
        setValue((v) => v.slice(0, -1));
        return;
      }
      if (key.leftArrow || key.rightArrow || key.pageUp || key.pageDown || key.home || key.end) return;
      if (input) setValue((v) => v + input.replace(/\r\n?/g, "\n"));
    },
    { isActive: active },
  );

  const lines = value.split("\n");
  return (
    <Box flexDirection="column">
      <Box flexDirection="column" borderStyle="round" borderColor={active ? theme.borderAccent : theme.border} paddingX={1}>
        {value === "" ? (
          <Text dimColor>
            {active ? "❯ " : "  "}
            {placeholder ?? ""}
          </Text>
        ) : (
          lines.map((line, i) => (
            <Text key={i}>
              {i === 0 ? "❯ " : "  "}
              {line}
              {active && i === lines.length - 1 ? <Text inverse> </Text> : null}
            </Text>
          ))
        )}
      </Box>
      {showPopup && (
        <Box flexDirection="column" borderStyle="round" borderColor={theme.border} paddingX={1}>
          {suggestions.map((s, i) => (
            <Text key={`${s.kind}:${s.complete}`} inverse={i === activeIndex}>
              {s.label}
            </Text>
          ))}
        </Box>
      )}
    </Box>
  );
}
