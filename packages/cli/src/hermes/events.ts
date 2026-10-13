import type { AgentEvent, PermissionRequest } from "../agent/types.js";

export interface HermesEvent {
  readonly type: string;
  readonly session_id?: string;
  readonly payload: Record<string, unknown>;
}

export interface HermesMessage {
  readonly role: "user" | "assistant" | "tool";
  text: string;
}

export function approvalOptions(request: PermissionRequest): Map<string, string> {
  const choices = new Map<string, string>();
  for (const option of request.options) {
    switch (option.kind) {
      case "allow_once": choices.set("once", option.optionId); break;
      case "allow_always": choices.set("always", option.optionId); break;
      case "reject_once":
      case "reject_always":
        if (!choices.has("deny")) choices.set("deny", option.optionId);
        break;
    }
  }
  return choices;
}

export function toolResultText(content: Extract<AgentEvent, { type: "tool_call_update" }>["content"]): string {
  return (content ?? []).map((item) => {
    switch (item.type) {
      case "text": return item.text;
      case "diff": return `${item.path}\n--- 이전\n${item.oldText ?? ""}\n+++ 변경\n${item.newText}`;
    }
  }).join("\n");
}
