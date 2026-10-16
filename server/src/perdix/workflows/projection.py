"""워크플로 이벤트 계약과 결정적인 현재 상태 투영."""

from __future__ import annotations

from typing import Annotated, Literal, assert_never

from pydantic import Field

from perdix.workflows.models import (
    AcceptanceReceipt,
    Nonempty,
    TaskAttempt,
    VerificationReceipt,
    WorkflowModel,
    WorkflowPlan,
    WorkflowRun,
)


class RunCreated(WorkflowModel):
    kind: Literal["run_created"] = "run_created"
    run: WorkflowRun


class RunUpdated(WorkflowModel):
    kind: Literal["run_updated"] = "run_updated"
    run: WorkflowRun


class AttemptUpdated(WorkflowModel):
    kind: Literal["attempt_updated"] = "attempt_updated"
    attempt: TaskAttempt


class VerificationRecorded(WorkflowModel):
    kind: Literal["verification"] = "verification"
    receipt: VerificationReceipt


class AcceptanceRecorded(WorkflowModel):
    kind: Literal["acceptance"] = "acceptance"
    receipt: AcceptanceReceipt


class OwnerAcquired(WorkflowModel):
    kind: Literal["owner_acquired"] = "owner_acquired"
    owner_id: Nonempty
    started_at: float = Field(ge=0)


class StepReserved(WorkflowModel):
    kind: Literal["step_reserved"] = "step_reserved"


class TimeRecorded(WorkflowModel):
    kind: Literal["time_recorded"] = "time_recorded"
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)
    final: bool = False


class WorkflowPhaseUpdated(WorkflowModel):
    kind: Literal["workflow_phase_updated"] = "workflow_phase_updated"
    phase_id: Nonempty
    phase: Literal["finish", "accept"]
    status: Literal["running", "completed", "failed", "unknown"]


class ProcessObserved(WorkflowModel):
    kind: Literal["process_observed"] = "process_observed"
    attempt_id: Nonempty
    pid: int = Field(gt=0)
    process_group: int = Field(gt=0)
    start_identity: str | None = None
    phase: Literal["started", "finished"]
    quiescent: bool = False


type EventPayload = Annotated[
    RunCreated
    | RunUpdated
    | AttemptUpdated
    | VerificationRecorded
    | AcceptanceRecorded
    | OwnerAcquired
    | StepReserved
    | TimeRecorded
    | ProcessObserved
    | WorkflowPhaseUpdated,
    Field(discriminator="kind"),
]


class WorkflowEvent(WorkflowModel):
    run_id: Nonempty
    idempotency_key: Nonempty
    owner_epoch: int = Field(default=0, ge=0)
    event_seq: int = Field(default=0, ge=0)
    payload: EventPayload


class WorkflowSnapshot(WorkflowModel):
    run: WorkflowRun
    plan: WorkflowPlan
    attempts: tuple[TaskAttempt, ...] = ()
    verification: VerificationReceipt | None = None
    acceptance: AcceptanceReceipt | None = None
    seq: int = 0
    steps_used: int = 0
    elapsed_seconds: float = 0
    owner_elapsed_seconds: float = 0
    owner_started_at: float = 0
    owner_active: bool = False
    processes: tuple[ProcessObserved, ...] = ()
    phases: tuple[WorkflowPhaseUpdated, ...] = ()


class WorkflowStateError(ValueError):
    """잘못된 이벤트 순서 또는 서로 다른 실행 계약."""


def project(snapshot: WorkflowSnapshot | None, event: WorkflowEvent) -> WorkflowSnapshot:
    """이벤트 하나를 적용하며 원본 모델은 변경하지 않는다."""
    match event.payload:
        case RunCreated(run=run):
            if snapshot is not None or run.run_id != event.run_id:
                raise WorkflowStateError("실행 생성 이벤트가 중복되거나 ID가 다릅니다")
            snapshot = WorkflowSnapshot(run=run, plan=run.plan)
        case _:
            if snapshot is None or snapshot.run.run_id != event.run_id:
                raise WorkflowStateError("실행 생성 이벤트가 필요합니다")
    if event.event_seq != snapshot.seq + 1:
        raise WorkflowStateError("이벤트 순서가 연속되지 않습니다")
    run = snapshot.run
    match event.payload:
        case RunCreated():
            pass
        case RunUpdated(run=updated):
            if (
                updated.run_id != run.run_id
                or updated.plan != run.plan
                or updated.project_root != run.project_root
                or updated.base_commit != run.base_commit
                or updated.target_branch != run.target_branch
                or updated.session_id != run.session_id
                or updated.graph_version != run.graph_version
                or updated.owner_epoch != run.owner_epoch
            ):
                raise WorkflowStateError("실행의 고정 계약은 변경할 수 없습니다")
            run = updated
        case AttemptUpdated(attempt=attempt):
            if (
                attempt.run_id != run.run_id
                or attempt.owner_epoch != event.owner_epoch
                or attempt.task_id not in {t.task_id for t in run.plan.tasks}
            ):
                raise WorkflowStateError("작업 실행 계약이 일치하지 않습니다")
            previous = next(
                (a for a in snapshot.attempts if a.attempt_id == attempt.attempt_id), None
            )
            if previous is not None and (
                previous.task_id != attempt.task_id
                or previous.status == "completed"
                and previous != attempt
            ):
                raise WorkflowStateError("완료 결과 또는 작업 ID를 덮어쓸 수 없습니다")
            saved = attempt.model_copy(update={"event_seq": event.event_seq})
            snapshot = snapshot.model_copy(
                update={
                    "attempts": tuple(
                        saved if a.attempt_id == saved.attempt_id else a for a in snapshot.attempts
                    )
                    if previous
                    else (*snapshot.attempts, saved)
                }
            )
        case VerificationRecorded(receipt=receipt):
            if receipt.run_id != run.run_id or receipt.owner_epoch != event.owner_epoch:
                raise WorkflowStateError("검증 영수증의 실행 계약이 다릅니다")
            snapshot = snapshot.model_copy(update={"verification": receipt})
            run = run.model_copy(update={"verification_status": receipt.status})
        case AcceptanceRecorded(receipt=acceptance):
            if acceptance.run_id != run.run_id or acceptance.owner_epoch != event.owner_epoch:
                raise WorkflowStateError("수락 영수증의 실행 계약이 다릅니다")
            snapshot = snapshot.model_copy(update={"acceptance": acceptance})
            run = run.model_copy(update={"acceptance_status": acceptance.status})
        case OwnerAcquired(started_at=started_at):
            if event.owner_epoch != run.owner_epoch + 1:
                raise WorkflowStateError("소유자 epoch는 순서대로 증가해야 합니다")
            run = run.model_copy(update={"owner_epoch": event.owner_epoch})
            snapshot = snapshot.model_copy(
                update={
                    "owner_elapsed_seconds": 0.0,
                    "owner_started_at": started_at,
                    "owner_active": True,
                }
            )
        case StepReserved():
            snapshot = snapshot.model_copy(update={"steps_used": snapshot.steps_used + 1})
        case TimeRecorded(elapsed_seconds=elapsed, final=final):
            if elapsed < snapshot.owner_elapsed_seconds:
                raise WorkflowStateError("누적 실행 시간은 감소할 수 없습니다")
            snapshot = snapshot.model_copy(
                update={
                    "elapsed_seconds": snapshot.elapsed_seconds
                    + elapsed
                    - snapshot.owner_elapsed_seconds,
                    "owner_elapsed_seconds": elapsed,
                    "owner_active": not final,
                }
            )
        case WorkflowPhaseUpdated() as phase:
            previous_phase = next(
                (p for p in snapshot.phases if p.phase_id == phase.phase_id), None
            )
            if previous_phase is not None and previous_phase.phase != phase.phase:
                raise WorkflowStateError("단계 ID의 종류는 변경할 수 없습니다")
            phases = tuple(p for p in snapshot.phases if p.phase_id != phase.phase_id)
            snapshot = snapshot.model_copy(update={"phases": (*phases, phase)})
        case ProcessObserved() as observed:
            process_owners = {a.attempt_id for a in snapshot.attempts} | {
                p.phase_id for p in snapshot.phases
            }
            if observed.attempt_id not in process_owners:
                raise WorkflowStateError("프로세스의 작업 실행이 없습니다")
            processes = tuple(
                p
                for p in snapshot.processes
                if (p.attempt_id, p.pid) != (observed.attempt_id, observed.pid)
            )
            snapshot = snapshot.model_copy(update={"processes": (*processes, observed)})
        case unreachable:
            assert_never(unreachable)
    return snapshot.model_copy(
        update={
            "run": run.model_copy(update={"event_seq": event.event_seq}),
            "seq": event.event_seq,
        }
    )
