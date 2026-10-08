"""세션별 Turn 직렬화와 취소 (harness-loop.md §3.1, §7).

`prompt()`는 첫 `await` 전에 동기적으로 Turn 슬롯(락)과 취소 이벤트를 등록한다.
SDK는 요청·알림마다 별도 task를 띄우므로, 늦게 등록하면 직후 도착한 `session/cancel`이 유실된다.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable

from perdix.context.messages import ContentBlock
from perdix.harness.turn import AskPermission, Sink, TurnResult, TurnRunner


class TurnBusyError(RuntimeError):
    """같은 세션에서 이미 Turn이 실행 중이다."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"session {session_id} already has a running turn")
        self.session_id = session_id


class SessionRunner:
    """세션별 `asyncio.Lock`과 취소 이벤트를 보유하고 Turn마다 `TurnRunner`를 만든다.

    `make_turn_runner(session_id)`는 동기 함수다. 세션의 현재 모드·모델·정책으로
    `TurnRunner`를 만들어 돌려준다(모드·모델 전환이 다음 Turn부터 반영된다).
    """

    def __init__(self, make_turn_runner: Callable[[str], TurnRunner]) -> None:
        self._make_turn_runner = make_turn_runner
        self._locks: dict[str, asyncio.Lock] = {}
        self._cancels: dict[str, asyncio.Event] = {}

    def is_running(self, session_id: str) -> bool:
        lock = self._locks.get(session_id)
        return lock is not None and lock.locked()

    @contextlib.asynccontextmanager
    async def guard(self, session_id: str) -> AsyncIterator[None]:
        """Turn과 같은 락으로 세션을 독점한다(`session/load` 등). 이미 잡혀 있으면 TurnBusyError.

        `prompt()`와 마찬가지로 확인과 획득 사이에 await가 없다.
        """
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        if lock.locked():
            raise TurnBusyError(session_id)
        async with lock:
            yield

    async def prompt(
        self,
        session_id: str,
        prompt: list[ContentBlock],
        *,
        sink: Sink,
        ask_permission: AskPermission,
    ) -> TurnResult:
        # 여기부터 lock 획득까지 await가 없어야 한다(동기 등록).
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        if lock.locked():
            raise TurnBusyError(session_id)
        cancel = asyncio.Event()
        self._cancels[session_id] = cancel
        # 잠기지 않은 락의 acquire는 양보 없이 즉시 성공한다.
        async with lock:
            try:
                runner = self._make_turn_runner(session_id)
                return await runner.run(
                    session_id,
                    prompt,
                    cancel=cancel,
                    sink=sink,
                    ask_permission=ask_permission,
                )
            finally:
                if self._cancels.get(session_id) is cancel:
                    del self._cancels[session_id]

    def cancel(self, session_id: str) -> None:
        """실행 중인 Turn의 취소 이벤트를 설정한다. 실행 중이 아니면 무시."""
        event = self._cancels.get(session_id)
        if event is not None:
            event.set()
