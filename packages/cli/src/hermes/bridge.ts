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
  let active: WebSocket | null = null;
  const send = (frame: unknown) => {
    if (active?.readyState === WebSocket.OPEN) active.send(JSON.stringify(frame));
  };
  adapter.onEvent = (event) => send({ jsonrpc: "2.0", method: "event", params: event });
  adapter.onRequest = (id, method, params) => send({ jsonrpc: "2.0", id, method, params });

  server.on("connection", (socket) => {
    if (active) { socket.close(1008, "UI already connected"); return; }
    active = socket;
    socket.on("error", () => { socket.terminate(); });
    socket.on("close", () => {
      if (active !== socket) return;
      active = null;
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
      void adapter.request(method, params).then(
        (result) => { if (id !== undefined) reply({ jsonrpc: "2.0", id, result }); },
        (error: unknown) => {
          if (id === undefined) return;
          const code = error instanceof HermesRpcError ? error.code : error instanceof z.ZodError ? -32602 : -32603;
          const message = error instanceof Error ? error.message : "Perdix 연결 요청에 실패했습니다.";
          reply({ jsonrpc: "2.0", id, error: { code, message } });
        },
      );
    });
    send({ jsonrpc: "2.0", method: "event", params: { type: "gateway.ready", payload: {
      heartbeat: true, skin: { branding: { agent_name: "Perdix", prompt_symbol: "❯",
        welcome: "Perdix", goodbye: "Perdix 대화를 저장했습니다.", help_header: "Perdix" },
        banner_logo: PERDIX_LOGO, banner_hero: PERDIX_HERO, colors: {} },
    } } });
  });

  return {
    url: `ws://127.0.0.1:${address.port}/hermes?token=${token}`,
    close: async () => {
      adapter.disconnect();
      for (const socket of server.clients) socket.terminate();
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    },
  };
}
