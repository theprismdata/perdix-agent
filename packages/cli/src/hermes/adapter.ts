import { z } from "zod";
import { randomUUID } from "node:crypto";
import type { LoadSessionOptions, NewSessionOptions } from "../agent/connection.js";
import type { AgentEvent, PermissionAnswer, PermissionRequest, SessionHandle, SessionListItem } from "../agent/types.js";
import { VERSION } from "../version.js";
import type { AuthAction, AuthProvider, HermesAuthResult } from "./auth.js";
import { approvalOptions, toolResultText, type HermesEvent, type HermesMessage } from "./events.js";
import { droppedImagePath, MAX_IMAGES, readClipboardImage, readImage, type AttachedImage } from "./images.js";

const sessionParams = z.object({ session_id: z.string().min(1) });
const promptParams = sessionParams.extend({ text: z.string() });
const configParams = z.object({ key: z.string(), value: z.unknown(), session_id: z.string().optional() });
const slashParams = sessionParams.extend({ command: z.string() });
const COMMANDS = [
  ["/team", "팀 안내·상태 조회·종료"],
  ["/help", "연결된 명령 안내"], ["/new", "새 대화"],
  ["/sessions", "저장된 대화 선택·복원"],
  ["/model", "모델 선택"], ["/agent", "Perdix 모드 조회·변경"],
  ["/permissions", "권한 모드 조회·변경 (interactive | yolo)"],
  ["/assign", "OpenRouter 모델을 에이전트에 할당"],
  ["/status", "현재 세션 상태"], ["/usage", "서버가 보고한 컨텍스트 사용량"],
  ["/cancel", "진행 중 작업 취소"],
  ["/image", "이미지 파일 첨부: /image <경로>"],
  ["/save", "자동 저장과 복원 안내"], ["/loop", "Perdix 목표 루프"],
  ["/memories", "Perdix 메모리"], ["/flows", "Langflow 플로우 목록"],
  ["/flow", "Langflow 플로우 실행: /flow <별칭> <입력>"], ["/theme", "auto | light | dark"],
  ["/redraw", "화면 다시 그리기"], ["/copy", "응답 복사"], ["/quit", "종료"],
  ["/login", "공급자 로그인·API 키 등록"], ["/logout", "공급자 로그아웃"], ["/auth", "status | list | use <제공자> <프로필|legacy>"],
] as const;

const COMMAND_ALIASES: Readonly<Record<string, string>> = {
  "/clear": "/new", "/resume": "/sessions", "/stop": "/cancel",
  "/exit": "/quit", "/mode": "/agent",
};

interface LiveSession {
  readonly images: Map<string, AttachedImage>;
  readonly handle: SessionHandle;
  readonly history: HermesMessage[];
  readonly tools: Map<string, { name: string; result: string; complete: boolean }>;
  unsubscribe: () => void;
  unpermission: () => void;
  replaying: boolean;
  busy: boolean;
  text: string;
  error: string | null;
  usage: { readonly used: number; readonly size: number; readonly cost?: number | null } | null;
}

export interface HermesAdapterOptions {
  readonly cwd: string;
  readonly mode?: string;
  readonly model?: string;
  readonly readClipboardImage?: () => Promise<AttachedImage>;
  readonly runAuth?: (action: AuthAction, provider: AuthProvider | undefined, signal: AbortSignal) => Promise<HermesAuthResult>;
  readonly reconnect?: (signal: AbortSignal) => Promise<HermesConnection>;
}

export interface HermesConnection {
  newSession(options: NewSessionOptions): Promise<SessionHandle>;
  loadSession(options: LoadSessionOptions): Promise<SessionHandle>;
  listSessions(cwd?: string): Promise<SessionListItem[]>;
}

export class HermesRpcError extends Error {
  constructor(readonly code: number, message: string) { super(message); }
}

export class HermesAdapter {
  private readonly sessions = new Map<string, LiveSession>();
  private current: string | null = null;
  private theme = "auto";
  private readonly pendingPermissions = new Map<string, (answer: PermissionAnswer) => void>();
  private permissionSequence = 0;
  private authController: AbortController | null = null;
  get authenticating(): boolean { return this.authController !== null; }
  onEvent: (event: HermesEvent) => void = () => {};
  onRequest: (id: string, method: string, params: Record<string, unknown>) => void = () => {};

  constructor(
    private connection: HermesConnection,
    private readonly options: HermesAdapterOptions,
  ) {}

  private session(id: string): LiveSession {
    const session = this.sessions.get(id);
    if (!session) throw new HermesRpcError(-32602, "이 연결에 없는 세션입니다. /resume으로 불러오세요.");
    return session;
  }

  private info(session: LiveSession): Record<string, unknown> {
    const info = session.handle.info;
    return { version: VERSION, model: info.currentModel ?? "", provider: "Perdix", cwd: info.cwd,
      stored_session_id: info.sessionId, running: session.busy, terminal_backend: "Perdix ACP",
      personality: info.currentMode, tools: {}, skills: {} };
  }

  private publish(id: string, type: string, payload: Record<string, unknown>): void {
    this.onEvent({ type, session_id: id, payload });
  }

  private replay(session: LiveSession, event: AgentEvent): void {
    switch (event.type) {
      case "user_text": session.history.push({ role: "user", text: event.text }); return;
      case "agent_text": {
        const last = session.history.at(-1);
        if (last?.role === "assistant") last.text += event.text;
        else session.history.push({ role: "assistant", text: event.text });
        return;
      }
      case "tool_call": session.tools.set(event.toolCallId, { name: event.title, result: "", complete: false }); return;
      case "tool_call_update": {
        const text = toolResultText(event.content);
        if (text) session.history.push({ role: "tool", text });
        return;
      }
      default: return;
    }
  }

  private receive(id: string, session: LiveSession, event: AgentEvent): void {
    if (session.replaying) { this.replay(session, event); return; }
    switch (event.type) {
      case "turn_started":
        session.busy = true; session.text = ""; session.error = null; session.tools.clear();
        this.publish(id, "message.start", {}); return;
      case "agent_text":
        session.text += event.text;
        this.publish(id, "message.delta", { text: event.text }); return;
      case "agent_thought": this.publish(id, "thinking.delta", { text: event.text }); return;
      case "user_text": return;
      case "tool_call": {
        const tool = { name: event.title, result: "", complete: false };
        session.tools.set(event.toolCallId, tool);
        this.publish(id, "tool.start", { tool_id: event.toolCallId, name: tool.name,
          context: event.title, args_text: JSON.stringify(event.rawInput ?? {}) });
        if (event.status === "completed" || event.status === "failed") {
          tool.complete = true;
          this.publish(id, "tool.complete", { tool_id: event.toolCallId, name: tool.name, summary: event.status });
        }
        return;
      }
      case "tool_call_update": {
        const tool = session.tools.get(event.toolCallId);
        if (!tool) return;
        if (event.content) tool.result = toolResultText(event.content);
        if (event.title) tool.name = event.title;
        if (!tool.complete && (event.status === "completed" || event.status === "failed")) {
          tool.complete = true;
          this.publish(id, "tool.complete", { tool_id: event.toolCallId, name: tool.name,
            summary: event.status === "failed" ? "실패" : "완료", result_text: tool.result });
        }
        return;
      }
      case "usage":
        session.usage = { used: event.used, size: event.size, cost: event.cost };
        this.publish(id, "session.usage", { usage: { context_used: event.used, context_max: event.size, cost_usd: event.cost } }); return;
      case "mode_changed":
      case "model_changed": this.publish(id, "session.info", this.info(session)); return;
      case "plan":
        this.publish(id, "todo.updated", { todos: event.entries.map((entry, index) => ({ id: String(index), task: entry.content, status: entry.status })), revision: 0 }); return;
      case "error": session.error = event.message; return;
      case "server_exit":
        session.busy = false;
        this.publish(id, "message.complete", { text: session.text, status: "error", error: `Perdix 서버가 종료됐습니다 (${event.code ?? "signal"}). 다시 실행해 /resume으로 복원하세요.` }); return;
      case "turn_ended":
        session.busy = false;
        if (session.text) session.history.push({ role: "assistant", text: session.text });
        this.publish(id, "message.complete", { text: session.text, status: event.stopReason === "cancelled" ? "interrupted" : event.stopReason === "error" ? "error" : "complete",
          error: session.error, warning: event.verification === "failed" ? "Perdix 검증이 실패했습니다." : undefined }); return;
      default: {
        const exhaustive: never = event;
        return exhaustive;
      }
    }
  }

  private attach(handle: SessionHandle, replaying: boolean): LiveSession {
    const session: LiveSession = { handle, history: [], tools: new Map(), images: new Map(), unsubscribe: () => {},
      unpermission: () => {}, replaying, busy: false, text: "", error: null, usage: null };
    const id = handle.info.sessionId;
    this.sessions.set(id, session);
    session.unsubscribe = handle.onEvent((event) => this.receive(id, session, event));
    session.replaying = false;
    session.unpermission = handle.onPermission((request) => this.askPermission(id, request));
    this.current = id;
    return session;
  }

  private askPermission(sessionId: string, request: PermissionRequest): Promise<PermissionAnswer> {
    const id = `perdix-approval-${++this.permissionSequence}`;
    const options = approvalOptions(request);
    return new Promise<PermissionAnswer>((resolve) => {
      this.pendingPermissions.set(id, resolve);
      this.permissionChoices.set(id, options);
      this.onRequest(id, "approval", { session_id: sessionId, description: request.title,
        command: JSON.stringify(request.rawInput ?? {}), allow_permanent: options.has("always"),
        choices: [...options.keys()].sort((left, right) => Number(right === "deny") - Number(left === "deny")) });
    });
  }

  private readonly permissionChoices = new Map<string, Map<string, string>>();

  private attachImage(session: LiveSession, attached: AttachedImage, remainder = ""): Record<string, unknown> {
    if (session.busy) throw new HermesRpcError(-32602, "작업이 끝난 뒤 이미지를 첨부하세요.");
    if (session.images.size >= MAX_IMAGES) throw new HermesRpcError(-32602, "이미지는 한 번에 4장까지 첨부할 수 있습니다.");
    const path = `attachment:${randomUUID()}`;
    session.images.set(path, attached);
    return { path, name: attached.name, remainder, count: session.images.size, attached: true };
  }

  answerPermission(id: string, result: unknown): void {
    const resolve = this.pendingPermissions.get(id);
    if (!resolve) return;
    const answer = z.object({ choice: z.string() }).safeParse(result);
    const optionId = answer.success ? this.permissionChoices.get(id)?.get(answer.data.choice) : undefined;
    this.pendingPermissions.delete(id); this.permissionChoices.delete(id);
    resolve(optionId ? { optionId } : { cancelled: true });
  }

  disconnect(): void {
    this.authController?.abort();
    for (const resolve of this.pendingPermissions.values()) resolve({ cancelled: true });
    this.pendingPermissions.clear(); this.permissionChoices.clear();
    for (const session of this.sessions.values()) {
      if (session.busy) void session.handle.cancel().catch((error: unknown) => {
        process.stderr.write(`Perdix 취소 요청 실패: ${error instanceof Error ? error.message : String(error)}\n`);
      });
      session.unsubscribe(); session.unpermission();
    }
    this.sessions.clear();
    this.current = null;
  }

  async request(method: string, params: Record<string, unknown>): Promise<unknown> {
    switch (method) {
      case "gateway.ping": return { pong: true };
      case "setup.status": return { provider_configured: true };
      case "config.get":
        if (params.key === "mtime") return { mtime: 0, mcp_rev: "" };
        if (params.key === "theme") return { value: this.theme };
        return { config: { display: { tui_theme: this.theme, busy_input_mode: "queue", tui_auto_resume_recent: false,
          tui_statusbar: "top", mouse_tracking: "all", show_cost: true, show_reasoning: true }, voice: {} } };
      case "config.full": return { config: { display: { tui_theme: this.theme, busy_input_mode: "queue", tui_auto_resume_recent: false,
        tui_statusbar: "top", mouse_tracking: "all", show_cost: true, show_reasoning: true }, voice: {} } };
      case "config.mtime": return { mtime: 0, mcp_rev: "" };
      case "commands.catalog": {
        const id = typeof params.session_id === "string" ? params.session_id : this.current;
        const session = id ? this.session(id) : null;
        return { pairs: COMMANDS, canon: { ...Object.fromEntries(COMMANDS.map(([name]) => [name, name])), ...COMMAND_ALIASES },
          categories: [{ name: "Perdix 호환 명령", pairs: COMMANDS }],
          sub: { "/agent": session?.handle.info.modes.map(mode => mode.id) ?? [],
            "/team": ["status", "stop", "leave", "help"],
            "/mode": session?.handle.info.modes.map(mode => mode.id) ?? [],
            "/permissions": ["interactive", "yolo", "deny", "accept-edits", "allowlist"],
            "/model": session?.handle.info.models.map(model => model.value) ?? [],
            "/theme": ["auto", "light", "dark"], "/loop": ["status", "cancel"],
            "/login": ["openai", "anthropic", "gemini", "openrouter"], "/logout": ["openai", "anthropic", "gemini", "openrouter"], "/auth": ["status openai", "status anthropic", "status gemini", "status openrouter"],
            "/memories": ["use on", "use off", "generate on", "generate off"] }, skill_count: 0 };
      }
      case "completion.get": return { items: [] };
      case "complete.slash": {
        const { text } = z.object({ text: z.string().default("") }).parse(params);
        if (!text.startsWith("/") || /\s/.test(text)) return { items: [], replace_from: 1 };
        const prefix = text.slice(1).toLowerCase();
        return { items: COMMANDS.filter(([command]) => command.slice(1).startsWith(prefix) ||
          Object.entries(COMMAND_ALIASES).some(([alias, canonical]) => canonical === command && alias.slice(1).startsWith(prefix)))
          .map(([command, description]) => ({ text: command.slice(1), display: command, meta: description, kind: "command" })), replace_from: 1 };
      }
      case "terminal.resize": return { status: "ok" };
      case "wake.start": return { status: "disabled" };
      case "client.metrics": return { status: "ok" };
      case "shared_metrics.startup_latency": return { recorded: false };
      case "shared_metrics.slash_command": return { recorded: false };
      case "session.control.read": return { control: { goal: null, loop: null, heartbeat: null, revision: "perdix-evaluation", updated_at: 0 } };
      case "subagent.list": return { subagents: [] };
      case "process.list": return { processes: [] };
      case "delegation.status": return { enabled: false };
      case "session.active_list": return { sessions: [] };
      case "session.create": {
        if ([...this.sessions.values()].some((session) => session.busy)) throw new HermesRpcError(-32602, "작업을 취소한 뒤 새 세션을 만드세요.");
        const handle = await this.connection.newSession({ cwd: this.options.cwd, mode: this.options.mode, model: this.options.model });
        const session = this.attach(handle, false);
        return { session_id: handle.info.sessionId, stored_session_id: handle.info.sessionId, info: this.info(session) };
      }
      case "session.resume":
      case "session.activate": {
        const { session_id: id } = sessionParams.parse(params);
        if ([...this.sessions.values()].some((session) => session.busy)) throw new HermesRpcError(-32602, "작업을 취소한 뒤 세션을 바꾸세요.");
        let session = this.sessions.get(id);
        if (!session) {
          session = this.attach(await this.connection.loadSession({ sessionId: id, cwd: this.options.cwd }), true);
          if (this.options.mode) await session.handle.setMode(this.options.mode);
          if (this.options.model) await session.handle.setModel(this.options.model);
        }
        this.current = id;
        return { session_id: id, stored_session_id: id, session_key: id, info: this.info(session), messages: session.history, running: false };
      }
      case "session.close": {
        const { session_id: id } = sessionParams.parse(params);
        const session = this.sessions.get(id);
        if (session?.busy) throw new HermesRpcError(-32602, "작업 중인 세션은 닫을 수 없습니다. 먼저 취소하세요.");
        return { status: "closed", session_id: id };
      }
      case "session.list": {
        const sessions = await this.connection.listSessions(this.options.cwd);
        return { sessions: sessions.map((item) => ({ id: item.sessionId, title: item.title ?? "", preview: item.title ?? "Perdix 대화",
          started_at: item.updatedAt ? Date.parse(item.updatedAt) / 1000 : 0, source: "Perdix" })) };
      }
      case "image.attach": {
        const input = sessionParams.extend({ path: z.string().min(1) }).parse(params);
        const session = this.session(input.session_id);
        const dropped = droppedImagePath(input.path);
        const image = await readImage(dropped?.path ?? input.path, session.handle.info.cwd);
        return this.attachImage(session, image, dropped?.remainder);
      }
      case "image.detach": {
        const input = sessionParams.extend({ path: z.string() }).parse(params);
        const session = this.session(input.session_id);
        return { detached: session.images.delete(input.path), count: session.images.size };
      }
      case "image.list": {
        const session = this.session(sessionParams.parse(params).session_id);
        return { images: [...session.images].map(([path, image]) => ({ path, name: image.name })) };
      }
      case "clipboard.paste": {
        const session = this.session(sessionParams.parse(params).session_id);
        const image = await (this.options.readClipboardImage ?? readClipboardImage)();
        return this.attachImage(session, image);
      }
      case "input.detect_drop": {
        const input = sessionParams.extend({ text: z.string() }).parse(params);
        const dropped = droppedImagePath(input.text);
        if (!dropped) return { matched: false };
        const session = this.session(input.session_id);
        const image = await readImage(dropped.path, session.handle.info.cwd);
        const attached = this.attachImage(session, image);
        return { ...attached, matched: true, is_image: true, text: dropped.remainder };
      }
      case "paste.collapse": return { collapsed: false };
      case "prompt.submit": {
        if (this.authenticating) throw new HermesRpcError(-32602, "인증이 끝난 뒤 요청하세요.");
        const { session_id: id, text } = promptParams.parse(params);
        const session = this.session(id);
        if (session.busy) throw new HermesRpcError(-32602, "session busy");
        if (!text.trim() && !session.images.size) throw new HermesRpcError(-32602, "메시지나 이미지를 입력하세요.");
        const images = [...session.images.values()];
        session.images.clear();
        session.history.push({ role: "user", text: [text, ...images.map(image => `[이미지: ${image.name}]`)].filter(Boolean).join("\n") });
        const control = /^\/loop(?:\s+(?:status|cancel))?\s*$/.test(text.trim());
        if (control) this.receive(id, session, { type: "turn_started" });
        const result = await session.handle.prompt(text, images.map(image => image.image));
        if (control) this.receive(id, session, { type: "turn_ended", stopReason: result.stopReason });
        return { status: "ok" };
      }
      case "session.status": {
        const session = this.session(sessionParams.parse(params).session_id);
        return { output: this.statusText(session) };
      }
      case "perdix.auth": {
        const input = z.object({ session_id: z.string(), action: z.enum(["login", "logout", "status"]), provider: z.enum(["openai", "anthropic", "gemini", "openrouter"]).optional() }).parse(params);
        this.session(input.session_id);
        if (this.authenticating || [...this.sessions.values()].some(session => session.busy)) throw new HermesRpcError(-32602, "작업이 끝난 뒤 인증을 변경하세요.");
        const runAuth = this.options.runAuth;
        if (!runAuth) throw new HermesRpcError(-32601, "이 실행에서는 인증 연결이 제공되지 않습니다.");
        const controller = new AbortController();
        this.authController = controller;
        try {
          const result = await runAuth(input.action, input.provider, controller.signal);
          if (controller.signal.aborted || result.code === 130) return { output: "인증을 취소했습니다." };
          if (result.code !== 0) return { output: `인증 ${input.action}에 실패했습니다 (종료 코드 ${result.code}). 기존 대화를 유지합니다.` };
          if (input.action !== "status" && this.options.reconnect) {
            const infos = [...this.sessions.values()].map(session => ({ ...session.handle.info }));
            const current = this.current;
            for (const session of this.sessions.values()) { session.unsubscribe(); session.unpermission(); }
            let next: HermesConnection;
            try { next = await this.options.reconnect(controller.signal); }
            catch (error) {
              for (const [id, session] of this.sessions) {
                session.unsubscribe = session.handle.onEvent(event => this.receive(id, session, event));
                session.unpermission = session.handle.onPermission(request => this.askPermission(id, request));
              }
              throw error;
            }
            this.connection = next;
            this.sessions.clear();
            for (const info of infos) {
              this.attach(await next.loadSession({ sessionId: info.sessionId, cwd: info.cwd }), true);
            }
            this.current = current;
          }
          const session = this.session(input.session_id);
          this.publish(input.session_id, "session.info", this.info(session));
          const provider = result.provider ?? input.provider;
          const suggested = session.handle.info.models.find(model => model.provider === provider ||
            (!model.provider && (provider === "openrouter" ? model.value.includes("/") :
              model.value.startsWith(provider === "openai" ? "gpt-" : provider === "gemini" ? "gemini-" : "claude-"))));
          return { output: input.action === "login"
            ? `로그인 완료 (${provider ?? "공급자"}).\n현재 모델: ${session.handle.info.currentModel ?? "미설정"}\n로그인은 모델을 자동 변경하지 않습니다. ${suggested ? `/model ${suggested.value}` : "/model"}로 선택하세요.`
            : input.action === "logout" ? "로그아웃 완료. 환경변수의 API 키가 있으면 계속 우선 적용됩니다." : result.output?.trim() || "인증 상태 출력을 받지 못했습니다." };
        } finally { this.authController = null; }
      }
      case "session.interrupt": {
        const session = this.session(sessionParams.parse(params).session_id);
        for (const resolve of this.pendingPermissions.values()) resolve({ cancelled: true });
        this.pendingPermissions.clear(); this.permissionChoices.clear();
        await session.handle.cancel();
        return { status: "interrupted" };
      }
      case "config.set": {
        const { key, value, session_id } = configParams.parse(params);
        if (key === "display.tui_theme" || key === "theme") {
          this.theme = z.enum(["auto", "light", "dark"]).parse(value);
          return { key, value: this.theme };
        }
        const id = session_id ?? this.current;
        if (!id) throw new HermesRpcError(-32602, "세션이 아직 준비되지 않았습니다.");
        const session = this.session(id);
        if (session.busy) throw new HermesRpcError(-32602, "작업이 끝난 뒤 설정을 바꾸세요.");
        const text = z.string().parse(value);
        let selected = text;
        if (key === "model") {
          const args = text.trim().split(/\s+/);
          if (args.includes("--global") || args.includes("--reasoning")) throw new HermesRpcError(-32602, "평가판에서는 현재 세션의 모델만 변경할 수 있습니다.");
          selected = args[0] ?? "";
          await session.handle.setModel(selected);
        }
        else if (key === "mode") await session.handle.setMode(text);
        else throw new HermesRpcError(-32601, "이 설정은 평가판에서 지원하지 않습니다.");
        this.publish(id, "session.info", this.info(session));
        return { key, value: selected };
      }
      case "model.list":
      case "models.list": {
        const session = this.current ? this.session(this.current) : null;
        return { models: session?.handle.info.models.map((model) => ({ id: model.value, name: model.name, provider: model.provider ?? "unknown" })) ?? [] };
      }
      case "model.options": {
        const id = typeof params.session_id === "string" ? params.session_id : this.current;
        const session = id ? this.session(id) : null;
        if (params.refresh === true) {
          if (!session?.handle.setConfigOption) throw new HermesRpcError(-32601, "모델 목록 조회를 지원하지 않습니다.");
          await session.handle.setConfigOption("openrouter_catalog", "refresh");
        }
        const models = (params.assign === true
          ? session?.handle.info.assignmentModels : session?.handle.info.models) ?? [];
        const currentModel = session?.handle.info.currentModel ?? "";
        const currentProvider = models.find(model => model.value === currentModel)?.provider ?? "unknown";
        const providers = [...new Set(models.map(model => model.provider ?? "unknown"))].map(slug => {
          const ids = models.filter(model => (model.provider ?? "unknown") === slug).map(model => model.value);
          return { slug, name: slug === "anthropic" ? "Anthropic" : slug === "openai" ? "OpenAI" : slug === "gemini" ? "Google Gemini" : slug === "openrouter" ? "OpenRouter" : slug,
            authenticated: true, is_current: slug === currentProvider, models: ids,
            capabilities: Object.fromEntries(ids.map(model => [model, { fast: false, reasoning: false }])) };
        });
        return { providers, model: currentModel, provider: currentProvider };
      }
      case "slash.exec": {
        const { session_id: id, command } = slashParams.parse(params);
        const session = this.session(id);
        const normalized = command.replace(/^\//, "").trim();
        const rawName = normalized.split(/\s+/)[0] ?? "";
        const name = rawName.toLowerCase();
        const argument = normalized.slice(rawName.length).trim();
        const args = argument ? argument.split(/\s+/) : [];
        if (name === "help") return { output: "Perdix + Hermes TUI 평가판\n" + COMMANDS.map(([cmd, description]) => `${cmd}  ${description}`).join("\n") + "\n음성·결제·Hermes 플러그인과 상세 설정은 이번 평가판에서 지원하지 않습니다." };
        if (name === "status") return { output: this.statusText(session) };
        if (name === "usage") return { output: session.usage
          ? `컨텍스트: ${session.usage.used} / ${session.usage.size} 토큰\n비용: ${session.usage.cost == null ? "서버가 제공하지 않음" : `$${session.usage.cost}`}\n입력·출력 토큰별 누적 통계는 현재 ACP 연결에서 제공하지 않습니다.`
          : "아직 서버가 보고한 사용량이 없습니다. 대화 후 /usage로 확인하세요." };
        if (name === "save") return { output: `Perdix는 대화를 자동 저장합니다.\n세션: ${id}\n복원: perdix --resume ${id}\n이 명령은 별도 파일을 내보내지 않습니다.` };
        if (name === "cancel" || name === "stop") {
          if (!session.busy) return { output: "진행 중인 작업이 없습니다." };
          await this.request("session.interrupt", { session_id: id });
          return { output: "Perdix 작업 취소를 요청했습니다." };
        }
        if (name === "mode" || name === "agent") {
          if (args.length === 0) return { output: `현재 모드: ${session.handle.info.currentMode}\n${session.handle.info.modes.map((mode) => mode.id).join(" | ")}` };
          if (session.busy) throw new HermesRpcError(-32602, "작업이 끝난 뒤 모드를 바꾸세요.");
          await session.handle.setMode(args.join(" "));
          this.publish(id, "session.info", this.info(session));
          return { output: `모드 변경: ${session.handle.info.currentMode}` };
        }
        if (name === "assign") {
          if (session.busy) throw new HermesRpcError(-32602, "작업 종료 후 설정하세요.");
          if (args.length !== 2 || !session.handle.setConfigOption) {
            throw new HermesRpcError(-32602, "/assign 메뉴에서 에이전트와 모델을 선택하세요.");
          }
          await session.handle.setConfigOption(`agent_model_${args[0]}`, args[1] ?? "");
          this.publish(id, "session.info", this.info(session));
          return { output: `${args[0]} 에이전트에 OpenRouter ${args[1]} 모델을 할당하고 agents.json에 저장했습니다.` };
        }
        if (name === "model") {
          if (args.length === 0) return { output: `현재 모델: ${session.handle.info.currentModel}\n${session.handle.info.models.map((model) => model.value).join("\n")}` };
          if (session.busy) throw new HermesRpcError(-32602, "작업이 끝난 뒤 모델을 바꾸세요.");
          if (args.includes("--global")) throw new HermesRpcError(-32602, "평가판의 모델 변경은 현재 세션에만 적용됩니다.");
          await session.handle.setModel(args[0] ?? "");
          this.publish(id, "session.info", this.info(session));
          return { output: `모델 변경: ${session.handle.info.currentModel}` };
        }
        if (name === "loop" || name === "memories" || name === "team" || name === "auth" || name === "permissions"
            || name === "flows" || name === "flow") {
          if (name === "loop" && argument === "cancel" && session.busy) return this.request("slash.exec", { session_id: id, command: "cancel" });
          if (session.busy) throw new HermesRpcError(-32602, "작업이 끝난 뒤 명령을 실행하세요. 취소는 /cancel을 사용하세요.");
          const text = `/${name}${argument ? ` ${argument}` : ""}`;
          return { type: "send", message: text, display: text };
        }
        throw new HermesRpcError(-32601, "이 Hermes 기능은 Perdix 평가판에 연결되지 않았습니다. /help로 지원 기능을 확인하세요.");
      }
      default: throw new HermesRpcError(-32601, `평가판에서 지원하지 않는 UI 요청입니다: ${method}`);
    }
  }

  private statusText(session: LiveSession): string {
    const info = session.handle.info;
    return `세션: ${info.sessionId}\n폴더: ${info.cwd}\n모델: ${info.currentModel ?? "미설정"}\n모드: ${info.currentMode}\n상태: ${session.busy ? "작업 중" : "대기 중"}`;
  }
}
