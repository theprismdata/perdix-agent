"""완료 ledger를 대조하고 불확실한 실행을 재생하지 않는 복구 경계."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from perdix.sandbox.base import ProcessObservation, ProcessObserver
from perdix.workflows.integration import IntegrationService
from perdix.workflows.projection import (
    AcceptanceRecorded,
    ProcessObserved,
    WorkflowEvent,
    WorkflowPhaseUpdated,
)
from perdix.workflows.store import WorkflowStore
from perdix.workflows.worktrees import CandidateCommit


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    resumable: bool
    read_only: bool
    completed_task_ids: tuple[str, ...] = ()
    blocked_attempt_ids: tuple[str, ...] = ()
    retryable_task_ids: tuple[str, ...] = ()


def process_observer(
    store: WorkflowStore,
    run_id: str,
    attempt_id: str,
    owner_epoch: int,
) -> ProcessObserver:
    async def observe(observation: ProcessObservation) -> None:
        await store.append_event(
            WorkflowEvent(
                run_id=run_id,
                owner_epoch=owner_epoch,
                idempotency_key=uuid4().hex,
                payload=ProcessObserved(
                    attempt_id=attempt_id,
                    pid=observation.pid,
                    process_group=observation.process_group,
                    start_identity=observation.start_identity,
                    phase=observation.phase,
                    quiescent=observation.quiescent,
                ),
            )
        )

    return observe


async def reconcile_run(
    store: WorkflowStore,
    run_id: str,
    *,
    acceptance_reconciliation: bool = False,
) -> RecoveryResult:
    """새 owner 잠금 안에서 호출한다. 불확실한 셸의 종료를 PID만으로 추정하지 않는다."""
    snapshot = await store.snapshot(run_id)
    if snapshot.run.graph_version != 1:
        return RecoveryResult(resumable=False, read_only=True)
    completed: list[str] = []
    blocked: list[str] = []
    retryable: list[str] = []
    for phase in snapshot.phases:
        observations = tuple(p for p in snapshot.processes if p.attempt_id == phase.phase_id)
        residual = any(p.phase != "finished" or not p.quiescent for p in observations)
        if phase.status in {"completed", "failed"} and not residual:
            continue
        if acceptance_reconciliation and phase.phase == "accept" and observations and not residual:
            continue
        blocked.append(phase.phase_id)
        if phase.status == "running":
            await store.append_event(
                WorkflowEvent(
                    run_id=run_id,
                    owner_epoch=snapshot.run.owner_epoch,
                    idempotency_key=f"phase-unknown:{phase.phase_id}",
                    payload=phase.model_copy(update={"status": "unknown"}),
                )
            )
    latest = {attempt.task_id: attempt for attempt in snapshot.attempts}
    for attempt in latest.values():
        ledger = await store.completed_attempt(run_id, attempt.attempt_id)
        observations = tuple(
            process for process in snapshot.processes if process.attempt_id == attempt.attempt_id
        )
        residual = any(p.phase != "finished" or not p.quiescent for p in observations)
        if attempt.status == "completed":
            if ledger != attempt or residual:
                blocked.append(attempt.attempt_id)
            else:
                completed.append(attempt.task_id)
            continue
        if attempt.status in {"failed", "cancelled", "interrupted"} and not residual:
            retryable.append(attempt.task_id)
            continue
        blocked.append(attempt.attempt_id)
        if attempt.status in {"pending", "ready", "running"}:
            await store.save_attempt(
                attempt.model_copy(
                    update={
                        "owner_epoch": snapshot.run.owner_epoch,
                        "status": "unknown",
                        "failure_reason": "중단된 실행의 부작용과 자식 종료를 입증할 수 없습니다",
                    }
                )
            )
    for process in snapshot.processes:
        if (
            process.phase != "finished" or not process.quiescent
        ) and process.attempt_id not in blocked:
            blocked.append(process.attempt_id)
    if blocked:
        run = (await store.snapshot(run_id)).run
        if run.status != "blocked":
            await store.save_run(run.model_copy(update={"status": "blocked"}))
    return RecoveryResult(
        resumable=not blocked,
        read_only=False,
        completed_task_ids=tuple(completed),
        blocked_attempt_ids=tuple(blocked),
        retryable_task_ids=tuple(retryable),
    )


async def reconcile_acceptance(
    store: WorkflowStore,
    run_id: str,
    integration: IntegrationService,
    candidate: CandidateCommit,
    *,
    cancel: asyncio.Event,
) -> bool:
    snapshot = await store.snapshot(run_id)
    intent, receipt = snapshot.acceptance, snapshot.verification
    if intent is None or receipt is None or intent.status not in {"awaiting", "accepted"}:
        return False
    if intent.status == "accepted" and intent.accepted_commit != candidate.commit:
        return False
    if not (await reconcile_run(store, run_id, acceptance_reconciliation=True)).resumable:
        return False
    head = await integration.worktrees.git(
        Path(snapshot.run.project_root),
        "rev-parse",
        "HEAD",
        cancel=cancel,
    )
    if head != candidate.commit:
        return False
    accepted = await integration.accept(
        snapshot.run,
        candidate,
        receipt,
        intent=intent.model_copy(update={"status": "awaiting"}),
        cancel=cancel,
        reconcile_only=True,
    )
    await store.append_event(
        WorkflowEvent(
            run_id=run_id,
            owner_epoch=snapshot.run.owner_epoch,
            idempotency_key=f"accept-recovered:{intent.receipt_id}:{snapshot.run.owner_epoch}",
            payload=AcceptanceRecorded(receipt=accepted),
        )
    )
    for phase in snapshot.phases:
        if phase.phase == "accept" and phase.status != "completed":
            await store.append_event(
                WorkflowEvent(
                    run_id=run_id,
                    owner_epoch=snapshot.run.owner_epoch,
                    idempotency_key=f"phase-recovered:{phase.phase_id}:{snapshot.run.owner_epoch}",
                    payload=WorkflowPhaseUpdated(
                        phase_id=phase.phase_id,
                        phase="accept",
                        status="completed",
                    ),
                )
            )
    return True
