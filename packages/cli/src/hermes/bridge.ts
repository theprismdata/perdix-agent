import { randomBytes } from "node:crypto";
import { once } from "node:events";
import type { IncomingMessage } from "node:http";
import { WebSocket, WebSocketServer } from "ws";
import { z } from "zod";
import { HermesAdapter, HermesRpcError } from "./adapter.js";
import { PERDIX_HERO, PERDIX_LOGO } from "./branding.js";

const idSchema = z.union([z.string(), z.number()]);
const requestSchema = z.object({ jsonrpc: z.literal("2.0"), id: idSchema.optional(), method: z.string(), params: z.record(z.string(), z.unknown()).default({}) });
const responseSchema = z.object({ jsonrpc: z.literal("2.0"), id: idSchema, result: z.unknown().optional(), error: z.unknown().optional() });

export interface HermesBridge {
  readonly url: string;
  close(): Promise<void>;
}

/** 한 번에 한 UI만 연결하는 Hermes JSON-RPC 소켓 처리. TUI 브리지와 데스크톱 서버가 함께 쓴다. */
export class HermesSocketHub {
  private active: WebSocket | null = null;

  constructor(
    private readonly adapter: HermesAdapter,
    private readonly readyPayload: () => Record<string, unknown>,
    private readonly trace: (line: string) => void = () => {},
  ) {
    adapter.onEvent = (event) => { this.trace(`<- event ${event.type}${event.type.startsWith("message.") || event.type === "error" ? ` ${JSON.stringify(event.payload).slice(0, 300)}` : ""}`); this.send({ jsonrpc: "2.0", method: "event", params: event }); };
    adapter.onRequest = (id, method, params) => { this.trace(`<- request ${method}`); this.send({ jsonrpc: "2.0", id, method, params }); };
  }

  private send(frame: unknown): void {
    if (this.active?.readyState === WebSocket.OPEN) this.active.send(JSON.stringify(frame));
  }

  get connected(): boolean { return this.active !== null; }

  attach(socket: WebSocket): void {
    const { adapter } = this;
    if (this.active) { socket.close(1008, "UI already connected"); return; }
    this.active = socket;
    socket.on("error", () => { socket.terminate(); });
    socket.on("close", () => {
      if (this.active !== socket) return;
      this.active = null;
      adapter.disconnect();
    });
    socket.on("message", (data, binary) => {
      const reply = (frame: unknown) => {
        if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(frame));
      };
      if (binary) { socket.close(1003, "Text frames only"); return; }
      let raw: unknown;
      try {
        raw = JSON.parse(data.toString());
      } catch (error) {
        if (!(error instanceof SyntaxError)) throw error;
        reply({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "JSON 메시지를 읽을 수 없습니다." } });
        return;
      }
      const request = requestSchema.safeParse(raw);
      if (!request.success) {
        const response = responseSchema.safeParse(raw);
        if (response.success) adapter.answerPermission(String(response.data.id), response.data.error ? undefined : response.data.result);
        else reply({ jsonrpc: "2.0", id: null, error: { code: -32600, message: "잘못된 UI 메시지입니다." } });
        return;
      }
      const { id, method, params } = request.data;
      this.trace(`-> ${method}`);
      void adapter.request(method, params).then(
        (result) => { if (id !== undefined) reply({ jsonrpc: "2.0", id, result }); },
        (error: unknown) => {
          this.trace(`!! ${method} ${error instanceof Error ? error.message : String(error)}`);
          if (id === undefined) return;
          const code = error instanceof HermesRpcError ? error.code : error instanceof z.ZodError ? -32602 : -32603;
          const message = error instanceof Error ? error.message : "Perdix 연결 요청에 실패했습니다.";
          reply({ jsonrpc: "2.0", id, error: { code, message } });
        },
      );
    });
    reply0(socket, { jsonrpc: "2.0", method: "event", params: { type: "gateway.ready", payload: this.readyPayload() } });
  }

  close(): void {
    this.adapter.disconnect();
    this.active?.terminate();
    this.active = null;
  }
}

function reply0(socket: WebSocket, frame: unknown): void {
  if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(frame));
}

function tuiReadyPayload(): Record<string, unknown> {
  return {
    heartbeat: true, skin: { branding: { agent_name: "Perdix", prompt_symbol: "❯",
      welcome: "Perdix", goodbye: "Perdix 대화를 저장했습니다.", help_header: "Perdix" },
    banner_logo: PERDIX_LOGO, banner_hero: PERDIX_HERO, colors: {} },
  };
}

export async function startHermesBridge(adapter: HermesAdapter): Promise<HermesBridge> {
  const token = randomBytes(32).toString("hex");
  const server = new WebSocketServer({
    host: "127.0.0.1", port: 0, maxPayload: 1024 * 1024,
    verifyClient: ({ req }: { req: IncomingMessage }) => {
      const url = new URL(req.url ?? "/", "http://127.0.0.1");
      return url.pathname === "/hermes" && url.searchParams.get("token") === token && !req.headers.origin;
    },
  });
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw new HermesRpcError(-32603, "평가판 연결 주소를 만들 수 없습니다.");
  const hub = new HermesSocketHub(adapter, tuiReadyPayload);
  server.on("connection", (socket) => hub.attach(socket));
  return {
    url: `ws://127.0.0.1:${address.port}/hermes?token=${token}`,
    close: async () => {
      hub.close();
      for (const socket of server.clients) socket.terminate();
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    },
  };
}
