// docs/protocol.md §2 IDE bridge (JSON-RPC 2.0 over WebSocket) 파라미터 스키마.
import { z } from "zod";

export const openDiffParams = z.object({
  path: z.string().min(1),
  oldText: z.string(),
  newText: z.string(),
  title: z.string(),
});

export const openFileParams = z.object({
  path: z.string().min(1),
  line: z.number().int().nonnegative().optional(),
});

export const getDirtyFilesParams = z.object({});

export const revertFileParams = z.object({ path: z.string().min(1) });

export const getSelectionParams = z.object({});

export const getDiagnosticsParams = z.object({
  path: z.string().min(1).optional(),
});

export const getOpenEditorsParams = z.object({});

export const selectionChangedParams = z.object({
  path: z.string().min(1),
  startLine: z.number().int().nonnegative(),
  endLine: z.number().int().nonnegative(),
  text: z.string().optional(),
});

export type OpenDiffParams = z.infer<typeof openDiffParams>;
export type OpenFileParams = z.infer<typeof openFileParams>;
export type GetDirtyFilesParams = z.infer<typeof getDirtyFilesParams>;
export type RevertFileParams = z.infer<typeof revertFileParams>;
export type GetSelectionParams = z.infer<typeof getSelectionParams>;
export type GetDiagnosticsParams = z.infer<typeof getDiagnosticsParams>;
export type GetOpenEditorsParams = z.infer<typeof getOpenEditorsParams>;
export type SelectionChangedParams = z.infer<typeof selectionChangedParams>;

/** 메서드명 → 파라미터 스키마. CLI→IDE 요청과 IDE→CLI 알림(selectionChanged)을 모두 포함. */
export const ideBridgeSchemas = {
  "ide/openDiff": openDiffParams,
  "ide/openFile": openFileParams,
  "ide/getDirtyFiles": getDirtyFilesParams,
  "ide/revertFile": revertFileParams,
  "ide/getSelection": getSelectionParams,
  "ide/getDiagnostics": getDiagnosticsParams,
  "ide/getOpenEditors": getOpenEditorsParams,
  "ide/selectionChanged": selectionChangedParams,
} as const;

export type IdeBridgeMethod = keyof typeof ideBridgeSchemas;
