"""자식 작업에 전달하는 공유 예산·취소·승인 실행 경계."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

from perdix.harness.budget import BudgetExceeded
from perdix.workflows.ownership import RunOwnership


class ExecutionContext:
    """하나의 owner 아래 작업 슬롯과 승인 큐를 공유한다."""

    def __init__(self, owner: RunOwnership, cancel: asyncio.Event) -> None:
        self.owner = owner
        self.cancel = cancel
        self._slots = asyncio.Semaphore(2)
        self._permissions = asyncio.Lock()

    async def check_owner(self) -> None:
        if self.cancel.is_set():
            raise asyncio.CancelledError
        await self.owner.check()
        if self.cancel.is_set():
            raise asyncio.CancelledError

    async def remaining_seconds(self) -> float:
        await self.check_owner()
        total = await self.owner.store.checkpoint_active_time(
            self.owner.run_id, self.owner.epoch, self.owner.elapsed_seconds,
        )
        snapshot = await self.owner.store.snapshot(self.owner.run_id)
        remaining = min(1800.0, snapshot.plan.wall_time_limit_seconds) - total
        if remaining <= 0:
            raise BudgetExceeded("workflow active wall time exhausted")
        return remaining

    async def reserve_step(self) -> None:
        """실제 공급자 호출 직전에 영속 카운터를 선점한다."""
        await self.remaining_seconds()
        snapshot = await self.owner.store.snapshot(self.owner.run_id)
        await self.owner.store.reserve_step(
            self.owner.run_id, self.owner.epoch, min(200, snapshot.plan.shared_steps_limit),
        )

    async def run[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        """작업을 취소·전체 남은 시간과 경합시키고 회수 완료까지 기다린다."""
        remaining = await self.remaining_seconds()
        work = asyncio.ensure_future(operation())
        cancelled = asyncio.create_task(self.cancel.wait())
        try:
            done, _ = await asyncio.wait(
                {work, cancelled}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED,
            )
            if cancelled in done:
                raise asyncio.CancelledError
            if work not in done:
                raise BudgetExceeded("workflow active wall time exhausted")
            result = await work
            await self.check_owner()
            return result
        finally:
            work.cancel()
            cancelled.cancel()
            with suppress(asyncio.CancelledError):
                await work
            with suppress(asyncio.CancelledError):
                await cancelled

    async def permission[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        async def serialized() -> T:
            async with self._permissions:
                await self.check_owner()
                return await operation()

        return await self.run(serialized)

    @asynccontextmanager
    async def task_slot(self) -> AsyncIterator[None]:
        """취소 가능한 대기로 실행 슬롯을 획득한다."""
        # acquire 완료와 취소가 겹쳐도 슬롯을 반환한다.
        acquired = False

        async def acquire() -> None:
            nonlocal acquired
            await self._slots.acquire()
            acquired = True

        try:
            await self.run(acquire)
            yield
        finally:
            if acquired:
                self._slots.release()


@dataclass(frozen=True, slots=True)
class TaskContext:
    """실행 증거를 task attempt에 결합하는 식별자."""

    execution: ExecutionContext
    task_id: str
    attempt_id: str

    @property
    def run_id(self) -> str:
        return self.execution.owner.run_id

    @property
    def owner_epoch(self) -> int:
        return self.execution.owner.epoch
