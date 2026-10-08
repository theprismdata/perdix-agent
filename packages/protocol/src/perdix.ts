// docs/protocol.md §1.2 의 _meta.perdix 확장 타입. 와이어 키는 camelCase.

/** initialize 요청 clientCapabilities._meta.perdix */
export interface PerdixClientCapabilities {
  checkpoints?: boolean;
  nestedToolCalls?: boolean;
}

/** session/new 요청 _meta.perdix */
export interface PerdixSessionNewMeta {
  model?: string;
  mode?: string;
  permissionMode?: string;
}

/** tool_call _meta.perdix */
export interface PerdixToolCallMeta {
  parentToolCallId?: string;
}

/** session/prompt 응답 _meta.perdix */
export interface PerdixPromptResponseMeta {
  verification?: "passed" | "failed" | "skipped";
}
