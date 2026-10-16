"""실행 전체에 걸친 OS 잠금과 영속 owner epoch."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from types import TracebackType
from uuid import uuid4

from filelock import FileLock

from perdix.workflows.store import WorkflowOwnerError, WorkflowStore


class RunOwnership:
    """잠금 수명 동안만 유효한 실행 소유권을 보유한다."""

    def __init__(self, store: WorkflowStore, run_id: str, lock_dir: Path) -> None:
        self.store = store
        self.run_id = run_id
        self.owner_id = f"{os.getpid()}-{uuid4().hex}"
        self.epoch = 0
        self._started = 0.0
        lock_dir.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(run_id.encode()).hexdigest()
        self._lock = FileLock(lock_dir / f"{name}.lock", timeout=0)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._started

    async def __aenter__(self) -> RunOwnership:
        self._lock.acquire()
        acquired = False
        try:
            self.epoch = await self.store.claim_owner(self.run_id, self.owner_id)
            self._started = time.monotonic()
            acquired = True
            return self
        finally:
            if not acquired:
                self._lock.release()

    async def __aexit__(
        self, exc_type: type[BaseException] | None,
        exc: BaseException | None, traceback: TracebackType | None,
    ) -> None:
        try:
            await self.store.finish_owner(self.run_id, self.epoch, self.elapsed_seconds)
        finally:
            self._lock.release()

    async def check(self) -> None:
        if not self._lock.is_locked or self.epoch == 0:
            raise WorkflowOwnerError("실행 소유권 잠금이 활성 상태가 아닙니다")
        await self.store.assert_owner(self.run_id, self.epoch)
