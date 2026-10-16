export const VERSION = "0.0.1";

/** Node 메이저 버전이 최소 요구 미만이면 오류 메시지를, 아니면 null을 반환한다. */
export function checkNodeVersion(version: string, minMajor = 22): string | null {
  const major = Number.parseInt(version.replace(/^v/, "").split(".")[0] ?? "", 10);
  if (Number.isNaN(major) || major < minMajor) {
    return `perdix: Node.js >= ${minMajor} is required (current: ${version}).`;
  }
  return null;
}
