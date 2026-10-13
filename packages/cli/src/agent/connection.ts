/**
 * ACP 클라이언트 연결. SDK `client()` 앱에 session/update·request_permission 핸들러를 달고
 * 세션 ID별로 AgentSession에 분배한다.
 */
import {
  AgentApp,
  client,
  ndJsonStream,
  PROTOCOL_VERSION,
  type ClientConnection,
  type ClientContext,
  type InitializeResponse,
  type NewSessionResponse,
  type PerdixClientCapabilities,
  type PerdixSessionNewMeta,
  type RequestPermissionRequest,
  type RequestPermissionResponse,
  type SessionNotification,
  type Stream,
} from "@perdix/protocol";
import { AgentSession, modelFromConfig } from "./session.js";
import { resolveServerCommand, spawnServer, type ServerProcess } from "./spawn.js";
import type { SessionInfo, SessionListItem } from "./types.js";
import { VERSION } from "../version.js";

/** 연결 대상: 바이트 스트림 위의 ACP 메시지 스트림, 또는 테스트용 in-process 에이전트 앱 */
export type AcpTransport = Stream | AgentApp;

export interface NewSessionOptions {
  cwd: string;
  mode?: string;
  model?: string;
  permissionMode?: string;
}

export interface LoadSessionOptions {
  sessionId: string;
  cwd: string;
}

export class PerdixConnection {
  private readonly sessions = new Map<string, AgentSession>();
  /** 세션 객체가 생기기 전에 도착한 알림(session/load 리플레이 등) */
  private readonly early = new Map<string, SessionNotification[]>();
  private conn: ClientConnection | null = null;
  private closing = false;
  initializeResult: InitializeResponse | null = null;

  private constructor(private readonly onClose: (() => Promise<void> | void) | undefined) {}

  static open(transport: AcpTransport, opts: { onClose?: () => Promise<void> | void } = {}): PerdixConnection {
    const self = new PerdixConnection(opts.onClose);
    const app = client({ name: "perdix" })
      .onNotification("session/update", (ctx) => {
        self.dispatchUpdate(ctx.params);
      })
      .onRequest("session/request_permission", (ctx) =>
        self.dispatchPermission(ctx.params, String(ctx.requestId)),
      );
    self.conn = transport instanceof AgentApp ? app.connect(transport) : app.connect(transport);
    return self;
  }

  get agent(): ClientContext {
    if (!this.conn) throw new Error("connection not open");
    return this.conn.agent;
  }

  get closed(): Promise<void> {
    return this.conn?.closed ?? Promise.resolve();
  }

  private dispatchUpdate(params: SessionNotification): void {
    const session = this.sessions.get(params.sessionId);
    if (session) {
      session.handleUpdate(params.update);
      return;
    }
    const queue = this.early.get(params.sessionId) ?? [];
    queue.push(params);
    this.early.set(params.sessionId, queue);
  }

  private async dispatchPermission(
    params: RequestPermissionRequest,
    requestId: string,
  ): Promise<RequestPermissionResponse> {
    const session = this.sessions.get(params.sessionId);
    if (!session) return { outcome: { outcome: "cancelled" } };
    return session.handlePermission(params, requestId);
  }

  async initialize(): Promise<InitializeResponse> {
    const perdix: PerdixClientCapabilities = { checkpoints: false, nestedToolCalls: true };
    this.initializeResult = await this.agent.request("initialize", {
      protocolVersion: PROTOCOL_VERSION,
      clientCapabilities: {
        fs: { readTextFile: false, writeTextFile: false },
        terminal: false,
        _meta: { perdix },
      },
      clientInfo: { name: "perdix", version: VERSION },
    });
    return this.initializeResult;
  }

  private register(info: SessionInfo): AgentSession {
    const session = new AgentSession({ agent: this.agent, info, onClose: () => this.close() });
    this.sessions.set(info.sessionId, session);
    const early = this.early.get(info.sessionId) ?? [];
    this.early.delete(info.sessionId);
    for (const n of early) session.handleUpdate(n.update);
    return session;
  }

  private static toInfo(
    sessionId: string,
    cwd: string,
    res: Pick<NewSessionResponse, "modes" | "configOptions">,
  ): SessionInfo {
    const { current, models } = modelFromConfig(res.configOptions);
    return {
      sessionId,
      cwd,
      currentMode: res.modes?.currentModeId ?? "",
      modes: (res.modes?.availableModes ?? []).map((m) => ({
        id: m.id,
        name: m.name,
        description: m.description ?? null,
      })),
      currentModel: current,
      models,
    };
  }

  async newSession(opts: NewSessionOptions): Promise<AgentSession> {
    const perdix: PerdixSessionNewMeta = {};
    if (opts.mode) perdix.mode = opts.mode;
    if (opts.model) perdix.model = opts.model;
    if (opts.permissionMode) perdix.permissionMode = opts.permissionMode;
    const res = await this.agent.request("session/new", {
      cwd: opts.cwd,
      mcpServers: [],
      ...(Object.keys(perdix).length > 0 ? { _meta: { perdix } } : {}),
    });
    return this.register(PerdixConnection.toInfo(res.sessionId, opts.cwd, res));
  }

  async loadSession(opts: LoadSessionOptions): Promise<AgentSession> {
    const previous = this.sessions.get(opts.sessionId);
    this.sessions.delete(opts.sessionId);
    try {
      const res = await this.agent.request("session/load", {
        sessionId: opts.sessionId,
        cwd: opts.cwd,
        mcpServers: [],
      });
      return this.register(PerdixConnection.toInfo(opts.sessionId, opts.cwd, res ?? {}));
    } catch (error) {
      if (previous) {
        this.sessions.set(opts.sessionId, previous);
        const early = this.early.get(opts.sessionId) ?? [];
        this.early.delete(opts.sessionId);
        for (const notification of early) previous.handleUpdate(notification.update);
      }
      throw error;
    }
  }

  async listSessions(cwd?: string): Promise<SessionListItem[]> {
    const items: SessionListItem[] = [];
    let cursor: string | null | undefined;
    do {
      const res = await this.agent.request("session/list", {
        ...(cwd ? { cwd } : {}),
        ...(cursor ? { cursor } : {}),
      });
      for (const s of res.sessions) {
        items.push({ sessionId: s.sessionId, cwd: s.cwd, title: s.title ?? null, updatedAt: s.updatedAt ?? null });
      }
      cursor = res.nextCursor;
    } while (cursor);
    return items;
  }

  /** 서버 종료를 모든 세션에 알린다. */
  notifyServerExit(code: number | null): void {
    for (const session of this.sessions.values()) session.emit({ type: "server_exit", code });
  }

  async close(): Promise<void> {
    if (this.closing) return;
    this.closing = true;
    this.conn?.close();
    await this.onClose?.();
  }
}

/** 기동된 서버 프로세스의 stdio 위에 ACP 연결을 열고 initialize까지 마친다. */
export async function connectToServer(server: ServerProcess): Promise<PerdixConnection> {
  const stream = ndJsonStream(server.input, server.output);
  const connection = PerdixConnection.open(stream, {
    onClose: async () => {
      server.kill();
      await server.exited;
    },
  });
  void server.exited.then((code) => {
    connection.notifyServerExit(code);
    connection.close().catch(() => {});
  });
  try {
    await connection.initialize();
  } catch (err) {
    // 서버가 기동 중 죽었으면 연결 오류 대신 종료 원인을 알린다.
    const timeout = new Promise<"running">((r) => setTimeout(() => r("running"), 1000).unref());
    const code = await Promise.race([server.exited, timeout]);
    if (code === "running") throw err;
    const reason = server.spawnError()?.message ?? `exit code ${String(code)}`;
    const hint = server.logPath ? ` (log: ${server.logPath})` : "";
    throw new Error(`perdix-server exited during startup: ${reason}${hint}`, { cause: err });
  }
  return connection;
}

export interface StartOptions {
  cwd: string;
  /** `--server <cmd>` */
  server?: string;
  debug?: boolean;
  /** 서버 argv `--permission-mode` */
  permissionMode?: string;
  /** 서버 argv에 그대로 덧붙일 인자 */
  serverArgs?: string[];
  trustProject?: boolean;
}

/** 서버를 찾아 띄우고 연결·initialize까지 한다. */
export async function startServerConnection(opts: StartOptions): Promise<PerdixConnection> {
  const command = resolveServerCommand({ server: opts.server, cwd: opts.cwd });
  const extraArgs = [...(opts.serverArgs ?? [])];
  if (opts.permissionMode) extraArgs.push("--permission-mode", opts.permissionMode);
  if (opts.trustProject) extraArgs.push("--trust-project");
  const server = spawnServer(command, { extraArgs, debug: opts.debug, cwd: opts.cwd });
  try {
    return await connectToServer(server);
  } catch (err) {
    server.kill();
    throw err;
  }
}
