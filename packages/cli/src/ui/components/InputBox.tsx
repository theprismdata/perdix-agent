import { useRef, useState } from "react";
import { Box, Text, useInput } from "ink";

export interface InputBoxProps {
  /** false면 키 입력을 받지 않는다(권한 프롬프트 표시 중). */
  active: boolean;
  onSubmit: (text: string) => void;
  placeholder?: string;
}

/**
 * 한 줄 입력창. 줄 끝이 `\`이면 Enter가 줄바꿈으로 바뀐다(Shift+Enter 대체).
 * ↑/↓ 로 이전 제출 기록을 순회한다.
 */
export function InputBox({ active, onSubmit, placeholder }: InputBoxProps) {
  const [value, setValue] = useState("");
  const history = useRef<string[]>([]);
  // history.current.length 는 "새 입력" 위치를 뜻한다.
  const [cursor, setCursor] = useState(0);
  const draft = useRef("");

  useInput(
    (input, key) => {
      if (key.ctrl || key.meta || key.tab || key.escape) return;
      if (key.return) {
        if (value.endsWith("\\")) {
          setValue(value.slice(0, -1) + "\n");
          return;
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
      if (key.upArrow) {
        if (cursor === 0) return;
        if (cursor === history.current.length) draft.current = value;
        setCursor(cursor - 1);
        setValue(history.current[cursor - 1] ?? "");
        return;
      }
      if (key.downArrow) {
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
    <Box flexDirection="column" borderStyle="round" borderColor={active ? "blue" : "gray"} paddingX={1}>
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
  );
}
