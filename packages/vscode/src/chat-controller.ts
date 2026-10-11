import { z } from "zod";
import type { AgentEvent, PermissionAnswer, PermissionRequest, SessionHandle, SessionInfo } from "../../cli/src/agent/types.js";

export const chatMessage = z.discriminatedUnion("type", [
  z.object({ type: z.literal("ready") }),
  z.object({ type: z.literal("send"), text: z.string().trim().min(1).max(100_000) }),
  z.object({ type: z.literal("cancel") }),
  z.object({ type: z.literal("new") }),
  z.object({ type: z.literal("history") }),
  z.object({ type: z.literal("keys") }),
  z.object({ type: z.literal("folder") }),
  z.object({ type: z.literal("settings") }),
  z.object({ type: z.literal("mode"), value: z.string().min(1) }),
  z.object({ type: z.literal("model"), value: z.string().min(1) }),
  z.object({ type: z.literal("permission"), requestId: z.string(), optionId: z.string() }),
]);

type Entry = AgentEvent | { type: "user_prompt"; text: string };
export interface ChatDependencies {
  connect(sessionId?: string): Promise<SessionHandle>;
  post(message: unknown): void;
  /** 서버 연결 전에도 설치 시 정한 기본 에이전트와 모델을 표시한다. */
  initialInfo?: SessionInfo;
}

/** 세션과 화면 수명주기를 분리한다. 권한 정책은 서버에 남긴다. */
export class ChatController {
  private session: SessionHandle | null = null;
  private entries: Entry[] = [];
  private pending = new Map<string, { request: PermissionRequest; resolve(answer: PermissionAnswer): void }>();
  private busy = false;
  private disposed = false;
  private cancelled = false;
  private connecting = false;
  private connectionError: string | null = null;

  constructor(private readonly deps: ChatDependencies) {}

  get isBusy(): boolean { return this.busy; }

  snapshot(): void {
    this.deps.post({ type: "snapshot", entries: this.entries, busy: this.busy,
      connecting: this.connecting, connectionError: this.connectionError,
      info: this.session?.info ?? this.deps.initialInfo ?? null, connected: this.session !== null,
      permissions: [...this.pending.values()].map((p) => p.request) });
  }

  private append(event: Entry): void {
    if (this.disposed) return;
    this.entries.push(event);
    this.deps.post({ type: "event", event });
  }

  error(error: unknown): void {
    this.append({ type: "error", message: error instanceof Error ? error.message : String(error) });
  }

  async initialize(): Promise<void> {
    if (this.disposed) return;
    this.snapshot();
    if (this.busy || this.session) return;
    this.busy = true;
    this.connecting = true;
    this.connectionError = null;
    this.state();
    try { await this.connect(); }
    catch (error) {
      this.connectionError = error instanceof Error ? error.message : String(error);
      this.error(error);
    } finally {
      this.busy = false;
      this.connecting = false;
      this.state();
    }
  }

  private async connect(sessionId?: string): Promise<SessionHandle> {
    if (this.session) return this.session;
    const session = await this.deps.connect(sessionId);
    if (this.disposed) { await session.close(); throw new Error("채팅이 종료되었습니다."); }
    this.session = session;
    session.onEvent((event) => {
      this.append(event);
      if (event.type === "server_exit") {
        this.cancelPermissions();
        this.session = null;
        this.connectionError = "서버 연결이 종료되었습니다.";
      }
      if (["mode_changed", "model_changed", "server_exit"].includes(event.type)) this.state();
    });
    session.onPermission((request) => new Promise<PermissionAnswer>((resolve) => {
      if (this.disposed || this.cancelled) { resolve({ cancelled: true }); return; }
      this.pending.set(request.requestId, { request, resolve });
      this.deps.post({ type: "permission", request });
    }));
    return session;
  }

  private state(): void {
    this.deps.post({ type: "state", busy: this.busy,
      connecting: this.connecting, connectionError: this.connectionError,
      info: this.session?.info ?? this.deps.initialInfo ?? null, connected: this.session !== null });
  }

  async send(text: string): Promise<void> {
    if (this.busy || this.disposed || !text.trim()) return;
    this.busy = true;
    this.cancelled = false;
    this.state();
    this.append({ type: "user_prompt", text });
    try {
      const session = await this.connect();
      if (this.cancelled || this.disposed) return;
      this.state();
      await session.prompt(text);
    } catch (error) { this.error(error); }
    finally { this.busy = false; this.state(); }
  }

  answer(requestId: string, optionId: string): void {
    const pending = this.pending.get(requestId);
    if (!pending || !pending.request.options.some((o) => o.optionId === optionId)) return;
    this.pending.delete(requestId);
    pending.resolve({ optionId });
    this.deps.post({ type: "permission_done", requestId });
  }

  private cancelPermissions(): void {
    for (const [requestId, pending] of this.pending) {
      pending.resolve({ cancelled: true });
      this.deps.post({ type: "permission_done", requestId });
    }
    this.pending.clear();
  }

  async cancel(): Promise<void> {
    this.cancelled = true;
    this.cancelPermissions();
    try { await this.session?.cancel(); } catch (error) { this.error(error); }
  }

  async reset(sessionId?: string): Promise<void> {
    if (this.busy || this.disposed) return;
    this.busy = true;
    this.cancelled = false;
    this.connecting = true;
    this.connectionError = null;
    this.state();
    try {
      this.cancelPermissions();
      const previous = this.session;
      this.session = null;
      await previous?.close();
      this.entries = [];
      this.snapshot();
      await this.connect(sessionId);
    } catch (error) {
      this.connectionError = error instanceof Error ? error.message : String(error);
      this.error(error);
    }
    finally { this.busy = false; this.connecting = false; this.state(); }
  }

  async configure(kind: "mode" | "model", value: string): Promise<void> {
    if (this.busy || !this.session) return;
    if (kind === "mode" && !this.session.info.modes.some((m) => m.id === value)) return;
    if (kind === "model" && !this.session.info.models.some((m) => m.value === value)) return;
    this.busy = true;
    this.state();
    try {
      if (kind === "mode") await this.session.setMode(value);
      else await this.session.setModel(value);
    } catch (error) { this.error(error); }
    finally { this.busy = false; this.state(); }
  }

  async dispose(): Promise<void> {
    this.disposed = true;
    this.cancelPermissions();
    await this.session?.close();
    this.session = null;
  }
}
