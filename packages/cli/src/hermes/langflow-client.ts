/**
 * Langflow REST API 클라이언트(읽기/쓰기). Langflow 소스는 건드리지 않고 공개 계약만 쓴다(ADR 0026).
 * 노드 에디터(workflow-plugin.ts)가 플로우를 불러오고 저장하는 데 쓴다.
 *
 * 엣지 핸들 인코딩: Langflow 프런트엔드는 sourceHandle/targetHandle을
 * `JSON.stringify(obj).replace(/"/g, "œ")`로 인코딩한다(`scapedJSONStringfy`,
 * langflow 소스 `src/frontend/src/utils/reactflowUtils.ts`). 같은 포맷으로 안 맞추면
 * 이 에디터로 저장한 플로우를 Langflow 자체 화면에서 다시 열 때 깨진다.
 */
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { z } from "zod";

const configSchema = z.looseObject({
  enabled: z.boolean().default(false),
  base_url: z.string().default("http://127.0.0.1:7860"),
  api_key_file: z.string().optional(),
});

export interface LangflowConfig {
  readonly baseUrl: string;
  readonly apiKey: string;
}

function expandHome(path: string): string {
  return path.startsWith("~") ? join(homedir(), path.slice(1)) : path;
}

/** 켜져 있고 API 키를 읽을 수 있는 Langflow 설정이면 돌려주고, 아니면 undefined. */
export function readLangflowConfig(path: string | undefined, warn: (message: string) => void = () => {}): LangflowConfig | undefined {
  if (path === undefined) return undefined;
  try {
    const config = configSchema.parse(JSON.parse(readFileSync(path, "utf8")));
    if (!config.enabled) return undefined;
    const apiKey = config.api_key_file ? readFileSync(expandHome(config.api_key_file), "utf8").trim() : "";
    return { baseUrl: config.base_url.replace(/\/+$/, ""), apiKey };
  } catch (error) {
    warn(`Langflow 설정을 읽지 못했습니다: ${error instanceof Error ? error.message : String(error)}`);
    return undefined;
  }
}

/** Langflow 프런트엔드의 scapedJSONStringfy와 같은 인코딩. */
export function encodeHandle(value: unknown): string {
  return JSON.stringify(value).replace(/"/g, "œ");
}

/** Langflow 프런트엔드의 scapeJSONParse와 같은 디코딩. */
export function decodeHandle(value: string): unknown {
  return JSON.parse(value.replace(/œ/g, '"'));
}

export class LangflowApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function call(config: LangflowConfig, path: string, init: { method?: string; body?: unknown } = {}): Promise<unknown> {
  const response = await fetch(`${config.baseUrl}${path}`, {
    method: init.method ?? "GET",
    headers: {
      "x-api-key": config.apiKey,
      ...(init.body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new LangflowApiError(`Langflow ${response.status}: ${text.slice(0, 300)}`, response.status);
  }
  if (response.status === 204) return undefined;
  return response.json();
}

export interface FlowSummary {
  readonly id: string;
  readonly name: string;
  readonly description: string;
}

export async function listFlows(config: LangflowConfig): Promise<FlowSummary[]> {
  // remove_example_flows 없이 부르면 Langflow 번들 예시/스타터 플로우까지 섞여 나오는데,
  // 그건 /flows/{id} 단건 조회가 404를 내서 고를 수 없다 — 애초에 목록에서 뺀다.
  const flows = await call(config, "/api/v1/flows/?remove_example_flows=true") as Array<Record<string, unknown>>;
  return flows.map((flow) => ({
    id: String(flow.id), name: String(flow.name ?? flow.id), description: String(flow.description ?? ""),
  }));
}

export interface FlowData {
  readonly nodes: Array<Record<string, unknown>>;
  readonly edges: Array<Record<string, unknown>>;
  readonly viewport?: Record<string, unknown>;
}

export interface FlowDetail extends FlowSummary {
  readonly data: FlowData;
}

export async function getFlow(config: LangflowConfig, id: string): Promise<FlowDetail> {
  const flow = await call(config, `/api/v1/flows/${encodeURIComponent(id)}`) as Record<string, unknown>;
  const data = (flow.data ?? { nodes: [], edges: [] }) as Record<string, unknown>;
  return {
    id: String(flow.id), name: String(flow.name ?? flow.id), description: String(flow.description ?? ""),
    data: {
      nodes: (data.nodes as Array<Record<string, unknown>>) ?? [],
      edges: (data.edges as Array<Record<string, unknown>>) ?? [],
      viewport: data.viewport as Record<string, unknown> | undefined,
    },
  };
}

/** 빈 플로우를 새로 만든다(`POST /api/v1/flows/`, FlowCreate 스키마). 만든 플로우의 id를 돌려준다. */
export async function createFlow(config: LangflowConfig, name: string, description = ""): Promise<FlowSummary> {
  const flow = await call(config, "/api/v1/flows/", {
    method: "POST", body: { name, description, data: { nodes: [], edges: [], viewport: { x: 0, y: 0, zoom: 1 } } },
  }) as Record<string, unknown>;
  return { id: String(flow.id), name: String(flow.name ?? name), description: String(flow.description ?? description) };
}

export async function deleteFlow(config: LangflowConfig, id: string): Promise<void> {
  await call(config, `/api/v1/flows/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function updateFlowData(config: LangflowConfig, id: string, data: FlowData): Promise<void> {
  await call(config, `/api/v1/flows/${encodeURIComponent(id)}`, { method: "PATCH", body: { data } });
}

/** `/api/v1/all`: {카테고리: {컴포넌트이름: 템플릿}}. 템플릿은 새 노드의 data.node로 그대로 쓸 수 있다. */
export type ComponentCatalog = Record<string, Record<string, Record<string, unknown>>>;

export async function listComponents(config: LangflowConfig): Promise<ComponentCatalog> {
  return await call(config, "/api/v1/all") as ComponentCatalog;
}
