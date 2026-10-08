"""event log → ModelView 투영(순수 함수). 규칙은 docs/harness-loop.md §5.1.

- assistant 메시지의 OpaqueItem은 아이템의 `(provider, model)`이 현재 요청과 같을 때만 남긴다.
  모든 아이템이 빠진 assistant 메시지는 투영에서 뺀다.
- `tool_result`는 완료 순서로 저장되지만, 대응하는 assistant 메시지 바로 뒤에
  `index` 순 `ToolResultsGroup` 하나로 모은다. 대응 tool_use가 없는 결과는 버린다.
- `compaction` 이벤트의 `[from_seq, to_seq]` 범위는 `UserMessage(origin="synthetic")` 요약
  하나로 치환한다. 나중 compaction이 앞선 범위와 겹치면 두 범위를 합치고 나중 요약을 쓴다
  (나중 요약은 앞선 요약이 들어간 투영으로부터 만들어졌기 때문). M1은 공급자와 관계없이
  summary 텍스트만 쓴다(불투명 압축 블록 재사용은 하지 않는다).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from perdix.context.messages import (
    AssistantMessage,
    OpaqueItem,
    ToolResult,
    ToolResultsGroup,
    ToolUseItem,
    UserMessage,
)
from perdix.session.store import Event

__all__ = ["ModelView", "ToolResultsGroup", "find_unpaired_tool_uses", "project"]


class ModelView(BaseModel):
    messages: list[UserMessage | AssistantMessage | ToolResultsGroup]


@dataclass
class _Range:
    from_seq: int
    to_seq: int
    summary: str

    def contains(self, seq: int) -> bool:
        return self.from_seq <= seq <= self.to_seq


def _compaction_ranges(events: Sequence[Event]) -> list[_Range]:
    ranges: list[_Range] = []
    for ev in events:
        if ev.kind != "compaction":
            continue
        c = ev.as_compaction()
        new = _Range(c.from_seq, c.to_seq, c.summary)
        kept: list[_Range] = []
        for r in ranges:
            if r.to_seq < new.from_seq or r.from_seq > new.to_seq:
                kept.append(r)
            else:
                new.from_seq = min(new.from_seq, r.from_seq)
                new.to_seq = max(new.to_seq, r.to_seq)
        kept.append(new)
        ranges = kept
    return sorted(ranges, key=lambda r: r.from_seq)


def project(events: list[Event], *, provider: str, model: str) -> ModelView:
    ordered = sorted(events, key=lambda e: e.seq)
    ranges = _compaction_ranges(ordered)

    def compacted(seq: int) -> bool:
        return any(r.contains(seq) for r in ranges)

    results: dict[str, ToolResult] = {}
    for ev in ordered:
        if ev.kind == "tool_result" and not compacted(ev.seq):
            res = ev.as_tool_result()
            results.setdefault(res.tool_use_id, res)

    messages: list[UserMessage | AssistantMessage | ToolResultsGroup] = []
    pending = list(ranges)

    def emit_summaries_up_to(seq: int) -> None:
        while pending and pending[0].from_seq <= seq:
            r = pending.pop(0)
            messages.append(UserMessage.from_text(r.summary, origin="synthetic"))

    for ev in ordered:
        if ev.kind not in ("user_message", "assistant_message"):
            continue
        emit_summaries_up_to(ev.seq)
        if compacted(ev.seq):
            continue
        if ev.kind == "user_message":
            messages.append(ev.as_user_message())
            continue
        msg = ev.as_assistant_message()
        items = [
            i
            for i in msg.items
            if not isinstance(i, OpaqueItem) or (i.provider == provider and i.model == model)
        ]
        if not items:
            continue
        msg = msg.model_copy(update={"items": items})
        messages.append(msg)
        group = [results[t.id] for t in msg.tool_uses() if t.id in results]
        if group:
            group.sort(key=lambda r: r.index)
            messages.append(ToolResultsGroup(results=group))
    while pending:
        messages.append(UserMessage.from_text(pending.pop(0).summary, origin="synthetic"))
    return ModelView(messages=messages)


def find_unpaired_tool_uses(events: list[Event]) -> list[tuple[ToolUseItem, bool]]:
    """결과가 없는 tool_use를 등장 순서로 돌려준다. 두 번째 값은 `tool_started` 존재 여부."""
    started = {e.as_tool_started().tool_use_id for e in events if e.kind == "tool_started"}
    finished = {e.as_tool_result().tool_use_id for e in events if e.kind == "tool_result"}
    unpaired: list[tuple[ToolUseItem, bool]] = []
    for ev in sorted(events, key=lambda e: e.seq):
        if ev.kind != "assistant_message":
            continue
        for tu in ev.as_assistant_message().tool_uses():
            if tu.id not in finished:
                unpaired.append((tu, tu.id in started))
    return unpaired
