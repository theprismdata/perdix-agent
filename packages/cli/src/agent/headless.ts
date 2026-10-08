/**
 * `perdix -p "<prompt>" [--json]` 비대화형 실행.
 * 이벤트를 --json이면 NDJSON(AgentEvent 한 줄씩)으로, 아니면 사람이 읽는 텍스트로 stdout에 쓴다.
 * 종료 코드: stopReason이 end_turn이면 0, 그 외 1.
 */
import { startServerConnection, type PerdixConnection } from "./connection.js";
import type { AgentSession } from "./session.js";
import type { AgentEvent } from "./types.js";

export interface HeadlessOptions {
  prompt: string;
  json: boolean;
  cwd: string;
  mode?: string;
  model?: string;
  resume?: string;
  /** 헤드리스 기본은 deny (protocol.md §1.3) */
  permissionMode?: string;
  server?: string;
  debug?: boolean;
  out?: NodeJS.WritableStream;
  err?: NodeJS.WritableStream;
}

/** 텍스트 모드 출력. 출력할 것이 없으면 빈 문자열 */
export function formatEventText(event: AgentEvent): string {
  switch (event.type) {
    case "agent_text":
      return event.text;
    case "user_text":
      return `> ${event.text}\n`;
    case "tool_call":
      return `\n[tool] ${event.title} (${event.kind})\n`;
    case "tool_call_update": {
      const lines: string[] = [];
      for (const c of event.content ?? []) {
        if (c.type === "text") lines.push(c.text);
        else lines.push(`[diff] ${c.path}`);
      }
      if (event.status === "completed" || event.status === "failed") {
        lines.push(`[tool ${event.status}] ${event.title ?? event.toolCallId}`);
      }
      return lines.length > 0 ? `${lines.join("\n")}\n` : "";
    }
    case "plan":
      return `${event.entries.map((e) => `- [${e.status}] ${e.content}`).join("\n")}\n`;
    case "mode_changed":
      return `[mode] ${event.modeId}\n`;
    case "model_changed":
      return `[model] ${event.model}\n`;
    case "turn_ended":
      return event.verification
        ? `\n[${event.stopReason}] verification: ${event.verification}\n`
        : `\n[${event.stopReason}]\n`;
    case "server_exit":
      return `\n[server exited: ${String(event.code)}]\n`;
    default:
      return "";
  }
}

export async function runHeadless(opts: HeadlessOptions): Promise<number> {
  const out = opts.out ?? process.stdout;
  const err = opts.err ?? process.stderr;
  let connection: PerdixConnection | null = null;
  let session: AgentSession | null = null;
  try {
    connection = await startServerConnection({
      cwd: opts.cwd,
      server: opts.server,
      debug: opts.debug,
      permissionMode: opts.permissionMode ?? "deny",
    });
    if (opts.resume) {
      session = await connection.loadSession({ sessionId: opts.resume, cwd: opts.cwd });
      // 리플레이는 출력하지 않는다. 새 턴의 이벤트만 내보낸다.
      session.takeBacklog();
    } else {
      session = await connection.newSession({ cwd: opts.cwd, mode: opts.mode, model: opts.model });
    }
    if (opts.resume && opts.mode) await session.setMode(opts.mode);
    if (opts.resume && opts.model) await session.setModel(opts.model);

    session.onEvent((event) => {
      if (opts.json) {
        out.write(`${JSON.stringify(event)}\n`);
      } else if (event.type === "error") {
        err.write(`perdix: ${event.message}\n`);
      } else {
        const text = formatEventText(event);
        if (text) out.write(text);
      }
    });
    const result = await session.prompt(opts.prompt);
    return result.stopReason === "end_turn" ? 0 : 1;
  } catch (e) {
    err.write(`perdix: ${e instanceof Error ? e.message : String(e)}\n`);
    return 1;
  } finally {
    if (session) await session.close();
    else if (connection) await connection.close();
  }
}
