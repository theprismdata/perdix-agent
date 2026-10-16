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

/** 작업 완료·검증·수락은 독립 상태다. */
export type WorkflowTaskStatus =
  | "pending" | "ready" | "running" | "completed" | "failed"
  | "blocked" | "interrupted" | "unknown" | "cancelled";
export type WorkflowVerificationStatus = "unverified" | "passed" | "failed" | "stale";
export type WorkflowAcceptanceStatus = "awaiting" | "accepted" | "rejected";

export interface WorkflowTask {
  readonly schemaVersion: 1;
  readonly taskId: string;
  readonly title: string;
  readonly role: "analyze" | "implement" | "review";
  readonly model: string;
  readonly paths: readonly string[];
  readonly acceptance: readonly string[];
  readonly dependencies: readonly string[];
  readonly artifactRequirements: readonly string[];
}

export interface WorkflowPlan {
  readonly schemaVersion: 1;
  readonly objective: string;
  readonly tasks: readonly WorkflowTask[];
  readonly maxParallelTasks: number;
  readonly maxRevisionRounds: number;
  readonly sharedStepsLimit: number;
  readonly wallTimeLimitSeconds: number;
}

export interface WorkflowRun {
  readonly schemaVersion: 1;
  readonly runId: string;
  readonly sessionId: string;
  readonly projectRoot: string;
  readonly baseCommit: string;
  readonly targetBranch: string;
  readonly plan: WorkflowPlan;
  readonly graphVersion: number;
  readonly ownerEpoch: number;
  readonly eventSeq: number;
  readonly status: WorkflowTaskStatus;
  readonly verificationStatus: WorkflowVerificationStatus;
  readonly acceptanceStatus: WorkflowAcceptanceStatus;
  readonly candidateCommit: string | null;
  readonly candidateTree: string | null;
  readonly failureReason?: string | null;
}

export interface WorkflowArtifactRef {
  readonly schemaVersion: 1;
  readonly artifactId: string;
  readonly path: string;
  readonly kind: string;
  readonly sha256: string | null;
  readonly description: string;
}

export interface WorkflowTaskAttempt {
  readonly schemaVersion: 1;
  readonly runId: string;
  readonly taskId: string;
  readonly attemptId: string;
  readonly ownerEpoch: number;
  readonly sessionId: string | null;
  readonly eventSeq: number;
  readonly status: WorkflowTaskStatus;
  readonly worktreePath: string | null;
  readonly baseCommit: string | null;
  readonly candidateCommit: string | null;
  readonly candidateTree: string | null;
  readonly artifacts: readonly WorkflowArtifactRef[];
  readonly failureReason: string | null;
}

export interface WorkflowVerificationCommandResult {
  readonly schemaVersion: 1;
  readonly command: string;
  readonly exitCode: number | null;
  readonly timedOut: boolean;
  readonly cancelled: boolean;
  readonly durationSeconds: number;
  readonly stdout: string;
  readonly stderr: string;
  readonly artifacts: readonly WorkflowArtifactRef[];
}

export interface WorkflowVerificationReceipt {
  readonly schemaVersion: 1;
  readonly receiptId: string;
  readonly runId: string;
  readonly candidateCommit: string;
  readonly candidateTree: string;
  readonly configDigest: string;
  readonly ownerEpoch: number;
  readonly eventSeq: number;
  readonly status: WorkflowVerificationStatus;
  readonly commands: readonly WorkflowVerificationCommandResult[];
  readonly artifacts: readonly WorkflowArtifactRef[];
}

export interface WorkflowAcceptanceReceipt {
  readonly schemaVersion: 1;
  readonly receiptId: string;
  readonly runId: string;
  readonly verificationReceiptId: string;
  readonly baseCommit: string;
  readonly candidateCommit: string;
  readonly candidateTree: string;
  readonly targetBranch: string;
  readonly status: WorkflowAcceptanceStatus;
  readonly ownerEpoch: number;
  readonly eventSeq: number;
  readonly acceptedCommit: string | null;
}

/** session/update의 update._meta.perdix.workflow. 알 수 없는 버전은 표준 텍스트로 표시한다. */
export interface PerdixWorkflowEnvelope {
  readonly schemaVersion: 1;
  readonly kind: "snapshot" | "event";
  readonly runId: string;
  readonly seq: number;
  readonly payload: unknown;
}
