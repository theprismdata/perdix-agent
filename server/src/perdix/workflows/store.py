"""세션 DB와 독립적인 append-only 워크플로 저장소."""

from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path
from typing import Final

from perdix.harness.budget import BudgetExceeded
from perdix.workflows.models import TaskAttempt, WorkflowRun
from perdix.workflows.projection import (
    AttemptUpdated,
    OwnerAcquired,
    RunCreated,
    RunUpdated,
    StepReserved,
    TimeRecorded,
    WorkflowEvent,
    WorkflowSnapshot,
    WorkflowStateError,
    project,
)
from perdix.workflows.store_sqlite import (
    WorkflowNotFoundError,
    WorkflowOwnerError,
    WorkflowSchemaError,
    check_owner,
    load_snapshot,
    persist,
    transaction,
)

__all__ = [
    "WorkflowStore",
    "WorkflowBudgetExceeded",
    "WorkflowNotFoundError",
    "WorkflowOwnerError",
    "WorkflowSchemaError",
]
DEFAULT_DB_PATH: Final = Path.home() / ".perdix" / "workflows.sqlite3"


class WorkflowBudgetExceeded(BudgetExceeded):
    """호출 전에 예약하는 공유 Step 예산이 소진되었다."""


class WorkflowStore:
    """각 호출이 연결을 소유하므로 동시 요청과 중단 뒤 재개가 안전하다."""

    def __init__(self, path: Path = DEFAULT_DB_PATH) -> None:
        self.path = path

    async def open(self) -> None:
        async with transaction(self.path):
            pass

    async def close(self) -> None:
        """연결은 각 호출의 context manager에서 이미 닫힌다."""

    async def create_run(self, run: WorkflowRun) -> WorkflowSnapshot:
        canonical = run.model_copy(update={"project_root": str(Path(run.project_root).resolve())})
        if canonical.owner_epoch or canonical.event_seq:
            raise WorkflowStateError("새 실행의 epoch와 sequence는 0이어야 합니다")
        async with transaction(self.path) as db:
            await persist(
                db,
                WorkflowEvent(
                    run_id=run.run_id, idempotency_key="create", payload=RunCreated(run=canonical)
                ),
            )
            snapshot = await load_snapshot(db, run.run_id)
        assert snapshot is not None
        return snapshot

    async def snapshot(self, run_id: str) -> WorkflowSnapshot:
        async with transaction(self.path) as db:
            result = await load_snapshot(db, run_id)
        if result is None:
            raise WorkflowNotFoundError(run_id)
        return result

    async def get_run(self, run_id: str, project_root: str | None = None) -> WorkflowRun | None:
        async with transaction(self.path) as db:
            snapshot = await load_snapshot(db, run_id)
        if snapshot is None or (
            project_root is not None
            and snapshot.run.project_root != str(Path(project_root).resolve())
        ):
            return None
        return snapshot.run

    async def list_runs(
        self,
        project_root: str,
        *,
        after_run_id: str = "",
        limit: int = 100,
    ) -> list[WorkflowRun]:
        if not 1 <= limit <= 1000:
            raise WorkflowStateError("limit은 1부터 1000까지입니다")
        async with transaction(self.path) as db:
            async with db.execute(
                "SELECT snapshot FROM workflow_runs WHERE project_root=? AND run_id>? "
                "ORDER BY run_id LIMIT ?",
                (str(Path(project_root).resolve()), after_run_id, limit),
            ) as c:
                return [WorkflowSnapshot.model_validate_json(r[0]).run for r in await c.fetchall()]

    async def append_event(self, event: WorkflowEvent) -> WorkflowEvent:
        async with transaction(self.path) as db:
            return await persist(db, event)

    async def save_run(self, run: WorkflowRun) -> WorkflowRun:
        event = await self.append_event(
            WorkflowEvent(
                run_id=run.run_id,
                owner_epoch=run.owner_epoch,
                idempotency_key=uuid.uuid4().hex,
                payload=RunUpdated(run=run),
            )
        )
        return run.model_copy(update={"event_seq": event.event_seq})

    async def save_attempt(self, attempt: TaskAttempt) -> TaskAttempt:
        payload = AttemptUpdated(attempt=attempt.model_copy(update={"event_seq": 0}))
        key = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        event = await self.append_event(
            WorkflowEvent(
                run_id=attempt.run_id,
                owner_epoch=attempt.owner_epoch,
                idempotency_key=f"attempt:{key}",
                payload=payload,
            )
        )
        return attempt.model_copy(update={"event_seq": event.event_seq})

    async def completed_attempt(self, run_id: str, attempt_id: str) -> TaskAttempt | None:
        async with transaction(self.path) as db:
            async with db.execute(
                "SELECT attempt FROM workflow_completions WHERE run_id=? AND attempt_id=?",
                (run_id, attempt_id),
            ) as c:
                row = await c.fetchone()
        return TaskAttempt.model_validate_json(row[0]) if row else None

    async def events(
        self,
        run_id: str,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[WorkflowEvent]:
        if after_sequence < 0 or not 1 <= limit <= 1000:
            raise WorkflowStateError("cursor는 음수가 아니며 limit은 1부터 1000까지입니다")
        async with transaction(self.path) as db:
            async with db.execute(
                "SELECT event FROM workflow_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                (run_id, after_sequence, limit),
            ) as c:
                return [WorkflowEvent.model_validate_json(r[0]) for r in await c.fetchall()]

    async def replay(self, run_id: str) -> WorkflowSnapshot:
        snapshot: WorkflowSnapshot | None = None
        cursor = 0
        while events := await self.events(run_id, cursor):
            for event in events:
                snapshot = project(snapshot, event)
                cursor = event.event_seq
        if snapshot is None:
            raise WorkflowNotFoundError(run_id)
        return snapshot

    async def claim_owner(self, run_id: str, owner_id: str) -> int:
        async with transaction(self.path) as db:
            snapshot = await load_snapshot(db, run_id)
            if snapshot is None:
                raise WorkflowNotFoundError(run_id)
            now = time.time()
            if snapshot.owner_active:
                # 비정상 종료 뒤의 downtime도 보수적으로 소비 처리한다.
                await persist(
                    db,
                    WorkflowEvent(
                        run_id=run_id,
                        owner_epoch=snapshot.run.owner_epoch,
                        idempotency_key=uuid.uuid4().hex,
                        payload=TimeRecorded(
                            elapsed_seconds=max(
                                snapshot.owner_elapsed_seconds, now - snapshot.owner_started_at
                            ),
                            final=True,
                        ),
                    ),
                )
            epoch = snapshot.run.owner_epoch + 1
            await persist(
                db,
                WorkflowEvent(
                    run_id=run_id,
                    owner_epoch=epoch,
                    idempotency_key=f"owner:{epoch}",
                    payload=OwnerAcquired(owner_id=owner_id, started_at=now),
                ),
            )
            return epoch

    async def assert_owner(self, run_id: str, epoch: int) -> None:
        check_owner(await self.snapshot(run_id), epoch)

    async def reserve_step(self, run_id: str, epoch: int, limit: int) -> int:
        async with transaction(self.path) as db:
            snapshot = await load_snapshot(db, run_id)
            if snapshot is None:
                raise WorkflowNotFoundError(run_id)
            check_owner(snapshot, epoch)
            if snapshot.steps_used >= min(limit, snapshot.plan.shared_steps_limit):
                raise WorkflowBudgetExceeded(run_id)
            await persist(
                db,
                WorkflowEvent(
                    run_id=run_id,
                    owner_epoch=epoch,
                    idempotency_key=f"step:{snapshot.steps_used + 1}",
                    payload=StepReserved(),
                ),
            )
            return snapshot.steps_used + 1

    async def checkpoint_active_time(
        self, run_id: str, epoch: int, elapsed_seconds: float
    ) -> float:
        return await self._record_time(run_id, epoch, TimeRecorded(elapsed_seconds=elapsed_seconds))

    async def finish_owner(self, run_id: str, epoch: int, elapsed_seconds: float) -> float:
        return await self._record_time(
            run_id, epoch, TimeRecorded(elapsed_seconds=elapsed_seconds, final=True)
        )

    async def _record_time(self, run_id: str, epoch: int, payload: TimeRecorded) -> float:
        async with transaction(self.path) as db:
            snapshot = await load_snapshot(db, run_id)
            if snapshot is None:
                raise WorkflowNotFoundError(run_id)
            check_owner(snapshot, epoch)
            if not snapshot.owner_active or (
                not payload.final and payload.elapsed_seconds <= snapshot.owner_elapsed_seconds
            ):
                return snapshot.elapsed_seconds
            recorded = payload.model_copy(
                update={
                    "elapsed_seconds": max(payload.elapsed_seconds, snapshot.owner_elapsed_seconds),
                }
            )
            await persist(
                db,
                WorkflowEvent(
                    run_id=run_id,
                    owner_epoch=epoch,
                    idempotency_key=f"time:{epoch}:{recorded.elapsed_seconds}:{recorded.final}",
                    payload=recorded,
                ),
            )
            return (
                snapshot.elapsed_seconds + recorded.elapsed_seconds - snapshot.owner_elapsed_seconds
            )
