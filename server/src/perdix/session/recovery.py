"""결과 없는 tool_use 복구 (harness-loop.md §3.4).

`session/load`와 매 Turn 시작에서 같은 함수를 쓴다. 과거 레코드는 수정하지 않고
오류 결과를 새 `tool_result` 레코드로 append한다.
"""

from __future__ import annotations

from typing import Final

from perdix.context.messages import ToolResult
from perdix.context.view import find_unpaired_tool_uses
from perdix.session.store import EventStore

INTERRUPTED_TEXT: Final = "interrupted: process exited"
NOT_EXECUTED_TEXT: Final = "not executed"


async def repair_unpaired_tool_uses(store: EventStore, session_id: str) -> int:
    """결과 없는 tool_use마다 오류 결과를 append하고 append한 개수를 돌려준다."""
    events = await store.events(session_id)
    unpaired = find_unpaired_tool_uses(events)
    if not unpaired:
        return 0
    indexes: dict[str, int] = {}
    for ev in events:
        if ev.kind == "assistant_message":
            for i, use in enumerate(ev.as_assistant_message().tool_uses()):
                indexes[use.id] = i
    for use, started in unpaired:
        await store.append(
            session_id,
            "tool_result",
            ToolResult(
                tool_use_id=use.id,
                index=indexes.get(use.id, 0),
                content=INTERRUPTED_TEXT if started else NOT_EXECUTED_TEXT,
                is_error=True,
            ),
        )
    return len(unpaired)
