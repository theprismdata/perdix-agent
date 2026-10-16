"""작업 이후 통합·수락 subprocess의 영속 관측 경계."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from perdix.sandbox.local import LocalBackend
from perdix.workflows.commands import WorkflowCommandError
from perdix.workflows.models import WorkflowRun
from perdix.workflows.projection import WorkflowEvent, WorkflowPhaseUpdated
from perdix.workflows.recovery import process_observer
from perdix.workflows.store import WorkflowStore
from perdix.workflows.worktrees import WorktreeService

if TYPE_CHECKING:
    from perdix.workflows.service import WorkflowContext


class WorkflowPhases:
    def __init__(self, store: WorkflowStore, storage_root: Path) -> None:
        self.store = store
        self.storage_root = storage_root

    @asynccontextmanager
    async def observe(
        self,
        run: WorkflowRun,
        phase: Literal["finish", "accept"],
        context: WorkflowContext,
    ) -> AsyncIterator[WorktreeService]:
        phase_id = "phase-" + uuid4().hex

        async def record(status: Literal["running", "completed", "failed", "unknown"]) -> None:
            await self.store.append_event(
                WorkflowEvent(
                    run_id=run.run_id,
                    owner_epoch=run.owner_epoch,
                    idempotency_key=f"{phase_id}-{status}",
                    payload=WorkflowPhaseUpdated(phase_id=phase_id, phase=phase, status=status),
                )
            )

        if not isinstance(context.turn.sandbox, LocalBackend):
            raise WorkflowCommandError("프로세스 관측 가능한 LocalBackend가 필요합니다")
        await record("running")
        backend = context.turn.sandbox.with_process_observer(
            process_observer(self.store, run.run_id, phase_id, run.owner_epoch),
        )
        successful = False
        try:
            yield WorktreeService(backend, self.storage_root / "worktrees")
            snapshot = await self.store.snapshot(run.run_id)
            if any(
                p.attempt_id == phase_id and (p.phase != "finished" or not p.quiescent)
                for p in snapshot.processes
            ):
                raise WorkflowCommandError(
                    "워크플로 단계의 자식 프로세스 종료가 확인되지 않았습니다"
                )
            successful = True
        finally:
            snapshot = await self.store.snapshot(run.run_id)
            residual = any(
                p.attempt_id == phase_id and (p.phase != "finished" or not p.quiescent)
                for p in snapshot.processes
            )
            await record("completed" if successful else "unknown" if residual else "failed")
