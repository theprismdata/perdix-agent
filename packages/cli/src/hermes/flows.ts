/**
 * Langflow 플로우 목록. Perdix 서버가 쓰는 같은 설정 파일(`--langflow-config`)에서 별칭과 설명만
 * 읽어 Hermes 화면에 보여준다. 실행·허용 판단은 Perdix 서버가 한다(여기서는 읽기 전용).
 */
import { readFileSync } from "node:fs";
import { z } from "zod";

const configSchema = z.object({
  enabled: z.boolean().default(false),
  base_url: z.string().default("http://127.0.0.1:7860"),
  flows: z.record(z.string(), z.string()).default({}),
  flow_descriptions: z.record(z.string(), z.string()).default({}),
}).passthrough();

export interface FlowEntry {
  readonly alias: string;
  readonly description: string;
}

export interface LangflowInfo {
  readonly baseUrl: string;
  readonly flows: FlowEntry[];
}

/** 켜져 있는 Langflow 설정이면 주소와 플로우 목록, 아니면 undefined. */
export function readLangflowInfo(path: string | undefined, warn: (message: string) => void = () => {}): LangflowInfo | undefined {
  if (path === undefined) return undefined;
  try {
    const config = configSchema.parse(JSON.parse(readFileSync(path, "utf8")));
    if (!config.enabled) return undefined;
    const flows = Object.keys(config.flows).map((alias) => ({ alias, description: config.flow_descriptions[alias] ?? "" }));
    return { baseUrl: config.base_url.replace(/\/+$/, ""), flows };
  } catch (error) {
    warn(`Langflow 설정을 읽지 못했습니다: ${error instanceof Error ? error.message : String(error)}`);
    return undefined;
  }
}

export function readFlowCatalog(path: string | undefined, warn: (message: string) => void = () => {}): FlowEntry[] {
  return readLangflowInfo(path, warn)?.flows ?? [];
}
