/** ink-testing-library 입력 보조: 렌더/이펙트가 반영될 시간을 준다. */
export const KEY = {
  up: "\u001B[A",
  down: "\u001B[B",
  enter: "\r",
  tab: "\t",
  escape: "\u001B",
  backspace: "\u007F",
  ctrlC: "\u0003",
  ctrlO: "\u000F",
};

export function tick(ms = 20): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 키를 하나씩 보내고 매번 반영을 기다린다. */
export async function press(stdin: { write: (data: string) => void }, ...keys: string[]): Promise<void> {
  for (const k of keys) {
    stdin.write(k);
    await tick();
  }
}
