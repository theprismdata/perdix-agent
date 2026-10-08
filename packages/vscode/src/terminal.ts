/** 터미널 환경 변수를 만든다. 브리지 서버가 아직 없으므로 포트는 placeholder(0). */
export function buildTerminalEnv(port = 0): Record<string, string> {
  return { PERDIX_IDE_PORT: String(port) };
}
