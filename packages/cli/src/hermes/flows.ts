/**
 * Langflow 플로우 목록. Perdix 서버가 쓰는 같은 설정 파일(`--langflow-config`)에서 별칭과 설명만
 * 읽어 Hermes 화면에 보여준다. 실행·허용 판단은 Perdix 서버가 한다(여기서는 읽기 전용).
 */
import { readFileSync } from "node:fs";
import { z } from "zod";

const configSchema = z.object({
  enabled: z.boolean().default(false),
  flows: z.record(z.string(), z.string()).default({}),
  flow_descriptions: z.record(z.string(), z.string()).default({}),
}).passthrough();

export interface FlowEntry {
  readonly alias: string;
  readonly description: string;
}

export function readFlowCatalog(path: string | undefined, warn: (message: string) => void = () => {}): FlowEntry[] {
  if (path === undefined) return [];
  try {
    const config = configSchema.parse(JSON.parse(readFileSync(path, "utf8")));
    if (!config.enabled) return [];
    return Object.keys(config.flows).map((alias) => ({ alias, description: config.flow_descriptions[alias] ?? "" }));
  } catch (error) {
    warn(`Langflow 설정을 읽지 못했습니다: ${error instanceof Error ? error.message : String(error)}`);
    return [];
  }
}
