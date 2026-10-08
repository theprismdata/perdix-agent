"""FakeProvider: 스크립트 재생, 요청 기록, 오류 주입, 파일 로딩."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from perdix.context.messages import TextBlock, ToolUseItem, UserMessage
from perdix.context.view import ModelView
from perdix.llm.base import (
    ModelEvent,
    ModelRequest,
    ProviderError,
    Stop,
    TextDelta,
    ToolUseEnd,
    ToolUseStart,
)
from perdix.llm.fake import FakeProvider


def req(text: str = "hi") -> ModelRequest:
    view = ModelView(
        messages=[UserMessage(role="user", content=[TextBlock(type="text", text=text)])]
    )
    return ModelRequest(system="s", tools=[], view=view, max_tokens=100, effort=None)


async def collect(p: FakeProvider, r: ModelRequest) -> list[ModelEvent]:
    return [e async for e in p.stream(r)]


async def test_script_order_and_request_recording() -> None:
    p = FakeProvider(
        [
            {
                "text": "reading",
                "tool_uses": [{"id": "t1", "name": "read", "input": {"path": "a"}}],
            },
            {"text": "done"},
        ]
    )
    first = await collect(p, req("one"))
    assert first[0] == TextDelta(text="reading")
    assert ToolUseStart(id="t1", name="read") in first
    assert ToolUseEnd(id="t1", input={"path": "a"}) in first
    stop = first[-1]
    assert isinstance(stop, Stop) and stop.reason == "tool_use" and stop.message is not None
    assert stop.message.items[1] == ToolUseItem(
        kind="tool_use", id="t1", name="read", input={"path": "a"}
    )

    second = await collect(p, req("two"))
    last = second[-1]
    assert isinstance(last, Stop) and last.reason == "end_turn"
    assert [r.view.messages[0] for r in p.requests] == [
        req("one").view.messages[0],
        req("two").view.messages[0],
    ]


async def test_truncated_tool_use_and_thinking() -> None:
    p = FakeProvider(
        [
            {
                "thinking_raw": {"type": "thinking", "thinking": "x", "signature": "s"},
                "tool_uses": [{"id": "t1", "name": "write", "input": {"path": "a"}}],
                "truncated_tool_use": True,
            },
            {"thinking_raw": {"type": "thinking", "thinking": "x", "signature": "s"}, "text": "k"},
        ]
    )
    events = await collect(p, req())
    assert events[-1] == Stop(reason="max_tokens", message=None, detail="truncated_tool_use")
    assert not any(isinstance(e, ToolUseEnd) for e in events)
    ok = (await collect(p, req()))[-1]
    assert isinstance(ok, Stop) and ok.message is not None
    assert ok.message.items[0].kind == "thinking"


async def test_error_before_and_after_first_chunk() -> None:
    p = FakeProvider(
        [
            {"error": {"retryable": True, "status": 529, "after_first_chunk": False}},
            {"error": {"retryable": False, "status": 400, "after_first_chunk": True}},
        ]
    )
    with pytest.raises(ProviderError) as info:
        await collect(p, req())
    assert info.value.retryable and info.value.status == 529

    seen: list[ModelEvent] = []
    with pytest.raises(ProviderError) as info2:
        async for e in p.stream(req()):
            seen.append(e)
    assert len(seen) == 1 and not info2.value.retryable


async def test_refusal_and_from_file(tmp_path: Path) -> None:
    path = tmp_path / "script.json"
    path.write_text(json.dumps([{"text": "no", "stop": "refusal", "detail": "cyber"}]))
    p = FakeProvider.from_file(path)
    stop = (await collect(p, req()))[-1]
    assert isinstance(stop, Stop) and stop.reason == "refusal" and stop.detail == "cyber"
    with pytest.raises(RuntimeError):
        await collect(p, req())
