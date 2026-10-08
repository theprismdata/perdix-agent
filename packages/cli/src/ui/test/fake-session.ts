/** 테스트용 SessionHandle: 이벤트와 권한 요청을 수동으로 흘려보낸다. */
import type {
  AgentEvent,
  PermissionAnswer,
  PermissionRequest,
  SessionHandle,
  SessionInfo,
} from "../../agent/types.js";

export class FakeSession implements SessionHandle {
  readonly info: SessionInfo;
  readonly prompts: string[] = [];
  readonly calls: string[] = [];
  private listeners = new Set<(event: AgentEvent) => void>();
  private permissionHandler: ((req: PermissionRequest) => Promise<PermissionAnswer>) | null = null;
  private resolvePrompt: ((r: { stopReason: string }) => void) | null = null;

  constructor(info: Partial<SessionInfo> = {}) {
    this.info = {
      sessionId: "0123456789abcdef",
      cwd: "/tmp/work",
      currentMode: "build",
      modes: [
        { id: "chat", name: "Chat" },
        { id: "plan", name: "Plan" },
        { id: "build", name: "Build" },
      ],
      currentModel: "fake-model",
      models: [{ value: "fake-model", name: "Fake" }, { value: "other-model", name: "Other" }],
      ...info,
    };
  }

  emit(event: AgentEvent): void {
    for (const l of this.listeners) l(event);
  }

  /** 등록된 권한 핸들러를 호출한다. 핸들러가 없으면 실제 구현처럼 reject_once. */
  requestPermission(req: PermissionRequest): Promise<PermissionAnswer> {
    if (!this.permissionHandler) return Promise.resolve({ optionId: "reject_once" });
    return this.permissionHandler(req);
  }

  /** prompt()가 기다리는 응답을 끝낸다. */
  finishTurn(stopReason = "end_turn"): void {
    this.emit({ type: "turn_ended", stopReason });
    this.resolvePrompt?.({ stopReason });
    this.resolvePrompt = null;
  }

  prompt(text: string): Promise<{ stopReason: string; verification?: string | null }> {
    this.prompts.push(text);
    this.emit({ type: "turn_started" });
    return new Promise((resolve) => {
      this.resolvePrompt = resolve;
    });
  }

  async cancel(): Promise<void> {
    this.calls.push("cancel");
    this.finishTurn("cancelled");
  }

  async setMode(modeId: string): Promise<void> {
    this.calls.push(`setMode:${modeId}`);
  }

  async setModel(model: string): Promise<void> {
    this.calls.push(`setModel:${model}`);
  }

  onEvent(listener: (event: AgentEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onPermission(handler: (req: PermissionRequest) => Promise<PermissionAnswer>): () => void {
    this.permissionHandler = handler;
    return () => {
      if (this.permissionHandler === handler) this.permissionHandler = null;
    };
  }

  async close(): Promise<void> {
    this.calls.push("close");
  }
}
