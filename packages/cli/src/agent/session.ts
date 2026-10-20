/**
 * AgentSession: ACP 세션 하나를 UI용 SessionHandle로 감싼다.
 * session/update → AgentEvent 변환과 session/request_permission 중계를 맡는다.
 */
import type {
  ClientContext,
  ContentBlock,
  RequestPermissionRequest,
  RequestPermissionResponse,
  SessionConfigOption,
  SessionUpdate,
  ToolCallContent,
} from "@perdix/protocol";
import type {
  AgentEvent,
  ModelOption,
  PermissionAnswer,
  PermissionRequest,
  PromptImage,
  SessionHandle,
  SessionInfo,
  ToolContent,
} from "./types.js";

export const MODEL_CONFIG_ID = "model";

type ToolStatus = "pending" | "in_progress" | "completed" | "failed";

function blockText(block: ContentBlock): string {
  return block.type === "text" ? block.text : `[${block.type}]`;
}

function meta(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

/** `_meta.perdix.<key>` 값을 꺼낸다. */
export function perdixMeta(m: unknown, key: string): unknown {
  const perdix = meta(meta(m)?.perdix);
  return perdix ? perdix[key] : undefined;
}

function toToolContent(items: ToolCallContent[]): ToolContent[] {
  const out: ToolContent[] = [];
  for (const item of items) {
    if (item.type === "diff") {
      out.push({ type: "diff", path: item.path, oldText: item.oldText ?? null, newText: item.newText });
    } else if (item.type === "content") {
      out.push({ type: "text", text: blockText(item.content) });
    }
    // terminal 콘텐츠는 Perdix 서버가 보내지 않는다(protocol.md §1.1).
  }
  return out;
}

function findModelOption(options: SessionConfigOption[]): SessionConfigOption | undefined {
  return (
    options.find((o) => o.id === MODEL_CONFIG_ID) ?? options.find((o) => o.category === "model")
  );
}

/** configOptions에서 현재 모델과 선택지를 뽑는다. */
export function modelFromConfig(options: SessionConfigOption[] | null | undefined): {
  current: string | null;
  models: ModelOption[];
} {
  const opt = findModelOption(options ?? []);
  if (!opt || opt.type !== "select") return { current: null, models: [] };
  const models: ModelOption[] = [];
  for (const entry of opt.options) {
    for (const option of "group" in entry ? entry.options : [entry]) {
      const provider = option.description?.match(/^provider: (\S+)/)?.[1];
      models.push({ value: option.value, name: option.name, ...(provider ? { provider } : {}) });
    }
  }
  return { current: opt.currentValue, models };
}

/** SessionUpdate 하나를 0개 이상의 AgentEvent로 바꾼다(types.ts 주석의 매핑). */
export function toAgentEvents(update: SessionUpdate): AgentEvent[] {
  switch (update.sessionUpdate) {
    case "user_message_chunk":
      return [{ type: "user_text", text: blockText(update.content) }];
    case "agent_message_chunk":
      return [{ type: "agent_text", text: blockText(update.content) }];
    case "agent_thought_chunk":
      return [{ type: "agent_thought", text: blockText(update.content) }];
    case "tool_call": {
      const parent = perdixMeta(update._meta, "parentToolCallId");
      const events: AgentEvent[] = [
        {
          type: "tool_call",
          toolCallId: update.toolCallId,
          title: update.title,
          kind: update.kind ?? "other",
          status: update.status ?? "pending",
          rawInput: update.rawInput,
          parentToolCallId: typeof parent === "string" ? parent : null,
        },
      ];
      // 최초 tool_call에 콘텐츠가 실려 오면 같은 카드의 업데이트로 이어 보낸다.
      if (update.content && update.content.length > 0) {
        events.push({
          type: "tool_call_update",
          toolCallId: update.toolCallId,
          content: toToolContent(update.content),
        });
      }
      return events;
    }
    case "tool_call_update": {
      const event: Extract<AgentEvent, { type: "tool_call_update" }> = {
        type: "tool_call_update",
        toolCallId: update.toolCallId,
      };
      if (update.status) event.status = update.status as ToolStatus;
      if (update.title) event.title = update.title;
      if (update.content) event.content = toToolContent(update.content);
      return [event];
    }
    case "plan":
      return [
        {
          type: "plan",
          entries: update.entries.map((e) => ({
            content: e.content,
            status: e.status,
            priority: e.priority,
          })),
        },
      ];
    case "usage_update":
      return [{ type: "usage", used: update.used, size: update.size, cost: update.cost?.amount ?? null }];
    case "current_mode_update":
      return [{ type: "mode_changed", modeId: update.currentModeId }];
    case "config_option_update": {
      const { current } = modelFromConfig(update.configOptions);
      return current === null ? [] : [{ type: "model_changed", model: current }];
    }
    default:
      return [];
  }
}

export interface AgentSessionOptions {
  agent: ClientContext;
  info: SessionInfo;
  /** close() 때 호출. 연결 종료·서버 정리 */
  onClose?: () => Promise<void> | void;
}

export class AgentSession implements SessionHandle {
  readonly info: SessionInfo;
  private readonly agent: ClientContext;
  private readonly onCloseHook: (() => Promise<void> | void) | undefined;
  private readonly listeners = new Set<(event: AgentEvent) => void>();
  /** 구독자가 없을 때 들어온 이벤트(session/load 리플레이 등). 첫 구독자에게 넘긴다. */
  private backlog: AgentEvent[] = [];
  private permissionHandler: ((req: PermissionRequest) => Promise<PermissionAnswer>) | null = null;
  private closed = false;

  constructor(opts: AgentSessionOptions) {
    this.agent = opts.agent;
    this.info = opts.info;
    this.onCloseHook = opts.onClose;
  }

  get sessionId(): string {
    return this.info.sessionId;
  }

  onEvent(listener: (event: AgentEvent) => void): () => void {
    this.listeners.add(listener);
    if (this.backlog.length > 0) {
      const pending = this.backlog;
      this.backlog = [];
      for (const event of pending) listener(event);
    }
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** 아직 전달되지 않은 리플레이 이벤트를 꺼내 비운다(헤드리스 재개 시 출력 생략용). */
  takeBacklog(): AgentEvent[] {
    const pending = this.backlog;
    this.backlog = [];
    return pending;
  }

  emit(event: AgentEvent): void {
    if (event.type === "mode_changed") this.info.currentMode = event.modeId;
    if (event.type === "model_changed") this.info.currentModel = event.model;
    if (this.listeners.size === 0) {
      this.backlog.push(event);
      return;
    }
    for (const listener of [...this.listeners]) listener(event);
  }

  /** 연결 계층이 session/update 알림을 넘긴다. */
  handleUpdate(update: SessionUpdate): void {
    if (update.sessionUpdate === "config_option_update") {
      const { models } = modelFromConfig(update.configOptions);
      if (models.length > 0) this.info.models = models;
    }
    for (const event of toAgentEvents(update)) this.emit(event);
  }

  /** 연결 계층이 session/request_permission 요청을 넘긴다. */
  async handlePermission(
    params: RequestPermissionRequest,
    requestId: string,
  ): Promise<RequestPermissionResponse> {
    const handler = this.permissionHandler;
    if (!handler) {
      const reject = params.options.find((o) => o.kind === "reject_once");
      return reject
        ? { outcome: { outcome: "selected", optionId: reject.optionId } }
        : { outcome: { outcome: "cancelled" } };
    }
    const req: PermissionRequest = {
      requestId,
      toolCallId: params.toolCall.toolCallId,
      title: params.toolCall.title ?? params.toolCall.toolCallId,
      kind: params.toolCall.kind ?? null,
      rawInput: params.toolCall.rawInput,
      options: params.options.map((o) => ({ optionId: o.optionId, name: o.name, kind: o.kind })),
    };
    const answer = await handler(req);
    if ("cancelled" in answer) return { outcome: { outcome: "cancelled" } };
    return { outcome: { outcome: "selected", optionId: answer.optionId } };
  }

  onPermission(handler: (req: PermissionRequest) => Promise<PermissionAnswer>): () => void {
    this.permissionHandler = handler;
    return () => {
      if (this.permissionHandler === handler) this.permissionHandler = null;
    };
  }

  async prompt(text: string, images: readonly PromptImage[] = []): Promise<{ stopReason: string; verification?: string | null }> {
    const control = /^\/loop(?:\s+(?:status|cancel))?\s*$/.test(text.trim());
    if (!control) this.emit({ type: "turn_started" });
    try {
      const res = await this.agent.request("session/prompt", {
        sessionId: this.sessionId,
        prompt: [...(text ? [{ type: "text" as const, text }] : []), ...images],
      });
      const raw = perdixMeta(res._meta, "verification");
      const verification =
        raw === "passed" || raw === "failed" || raw === "skipped" ? raw : null;
      if (!control) this.emit({ type: "turn_ended", stopReason: res.stopReason, verification });
      return { stopReason: res.stopReason, verification };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.emit({ type: "error", message });
      if (!control) this.emit({ type: "turn_ended", stopReason: "error", verification: null });
      return { stopReason: "error", verification: null };
    }
  }

  async cancel(): Promise<void> {
    await this.agent.notify("session/cancel", { sessionId: this.sessionId });
  }

  async setMode(modeId: string): Promise<void> {
    await this.agent.request("session/set_mode", { sessionId: this.sessionId, modeId });
    this.info.currentMode = modeId;
  }

  async setModel(model: string): Promise<void> {
    await this.setConfigOption(MODEL_CONFIG_ID, model);
  }

  async setConfigOption(configId: string, value: string): Promise<void> {
    const res = await this.agent.request("session/set_config_option", {
      sessionId: this.sessionId,
      configId,
      value,
    });
    const { current, models } = modelFromConfig(res.configOptions);
    const assignment = res.configOptions.find(option => option.id === 'agent_model_chat');
    if (assignment?.type === 'select') {
      this.info.assignmentModels = modelFromConfig([
        { ...assignment, id: MODEL_CONFIG_ID, category: 'model' },
      ]).models.filter(option => option.provider === 'openrouter');
    }
    if (models.length > 0) this.info.models = models;
    this.info.currentModel = current ?? this.info.currentModel;
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    this.listeners.clear();
    this.permissionHandler = null;
    await this.onCloseHook?.();
  }
}
