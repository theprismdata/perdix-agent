/** 설치 화면에서 정한 CLI 기본값. 모델 실행 로직은 서버에 둔다. */
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export function readDefaultModel(path = join(homedir(), ".perdix", "preferences.json")): string | undefined {
  try {
    const data: unknown = JSON.parse(readFileSync(path, "utf8"));
    if (!data || typeof data !== "object" || !("default_model" in data)) return undefined;
    const model = data.default_model;
    return typeof model === "string" && model.trim() ? model.trim() : undefined;
  } catch {
    return undefined;
  }
}
