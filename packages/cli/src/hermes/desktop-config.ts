/**
 * Hermes 데스크톱 화면 설정 기록(`GET/PUT /api/config`). Hermes 앱은 언어 같은 화면 설정을 백엔드
 * 설정 파일에 저장한다. Perdix에는 그런 파일이 없어서, 호환 서버가 받은 기록을 그대로 보관했다가
 * 돌려준다. 에이전트 동작에는 쓰지 않는다 — 실제로 효과가 있는 것은 Hermes 앱이 다시 읽는 값
 * (예: `display.language`)뿐이다. Perdix 에이전트 설정(agents.json 등)은 여기에 두지 않는다.
 *
 * `hermesHome`이 있으면 `<hermesHome>/perdix-desktop-config.json`에 저장하고, 없으면 메모리에만 둔다.
 */
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

type Json = Record<string, unknown>;

const isRecord = (value: unknown): value is Json =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** 깊은 병합. Hermes의 `PUT /api/config`도 디스크 기록에 깊게 병합한다(빠진 키는 그대로 둔다). */
export function mergeConfig(base: Json, patch: Json): Json {
  const out: Json = { ...base };
  for (const [key, value] of Object.entries(patch)) {
    out[key] = isRecord(value) && isRecord(out[key]) ? mergeConfig(out[key] as Json, value) : value;
  }
  return out;
}

export class DesktopConfigStore {
  private record: Json;
  private readonly path: string | undefined;

  constructor(hermesHome?: string) {
    this.path = hermesHome === undefined ? undefined : join(hermesHome, "perdix-desktop-config.json");
    this.record = this.load();
  }

  read(): Json {
    return structuredClone(this.record);
  }

  /** 받은 기록을 깊게 병합해 저장한다. Hermes의 `preserve_language`는 "언어 값을 기본값과 같아도 파일에
   *  명시적으로 남겨라"라는 뜻이라(hermes_cli/web_routers/config_env.py), 기본값을 따로 걸러 내지 않는
   *  이 저장소에서는 따로 할 일이 없다. */
  save(patch: Json): Json {
    this.record = mergeConfig(this.record, structuredClone(patch));
    this.persist();
    return this.read();
  }

  private load(): Json {
    if (this.path === undefined) return {};
    try {
      const parsed: unknown = JSON.parse(readFileSync(this.path, "utf8"));
      return isRecord(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }

  private persist(): void {
    if (this.path === undefined) return;
    mkdirSync(dirname(this.path), { recursive: true });
    const temporary = `${this.path}.tmp`;
    writeFileSync(temporary, `${JSON.stringify(this.record, null, 2)}\n`);
    renameSync(temporary, this.path);
  }
}
