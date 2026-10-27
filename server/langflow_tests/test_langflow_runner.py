"""LangflowTurnRunner: Langflow 스트림 이벤트 → 하네스 이벤트 변환 (ADR 0027)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from perdix.context.messages import TextBlock
from perdix.harness.events import HarnessEvent, TextDelta, ToolFinished, ToolStarted, TurnEnded
from perdix.harness.langflow import LangflowTurnRunner
from perdix.llm.base import ProviderError
from perdix.session.store import EventStore


def body(*events: dict[str, Any]) -> bytes:
    return "".join(json.dumps(e) + "\n\n" for e in events).encode()


async def run(tmp_path: Path, handler: Any, cancel: asyncio.Event | None = None):
    store = await EventStore.open(str(tmp_path / "db.sqlite3"))
    session = await store.create_session(str(tmp_path), "build", "langflow", "langflow")
    events: list[HarnessEvent] = []

    async def sink(event: HarnessEvent) -> None:
        events.append(event)

    async def ask(_: Any) -> Any:
        raise AssertionError("Langflow 백엔드는 권한을 묻지 않는다")

    runner = LangflowTurnRunner(
        store=store, base_url="http://lf.test", flow="flow-1", api_key="k",
        transport=httpx2.MockTransport(handler),
    )
    try:
        result = await runner.run(
            session.id, [TextBlock(text="안녕")], cancel=cancel or asyncio.Event(), sink=sink,
            ask_permission=ask,
        )
    finally:
        await store.close()
    return result, events


@pytest.mark.asyncio
async def test_tokens_stream_as_text_and_request_carries_session(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["x-api-key"]
        seen["json"] = json.loads(request.content)
        seen["session"] = seen["json"]["session_id"]
        return httpx2.Response(200, content=body(
            {"event": "token", "data": {"chunk": "하이"}},
            {"event": "token", "data": {"chunk": "!"}},
            {"event": "end", "data": {}},
        ))

    result, events = await run(tmp_path, handler)
    assert result.stop_reason == "end_turn"
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["하이", "!"]
    assert isinstance(events[-1], TurnEnded)
    assert seen["url"].startswith("http://lf.test/api/v1/run/flow-1")
    assert "stream=true" in seen["url"] and seen["key"] == "k"
    assert seen["json"]["session_id"] == seen["session"] and seen["json"]["input_value"] == "안녕"


@pytest.mark.asyncio
async def test_final_message_used_when_no_tokens_and_tools_mapped(tmp_path: Path) -> None:
    tool = {"type": "tool_use", "name": "search", "tool_input": {"q": "x"}, "output": "결과"}
    message = {"sender": "Machine", "text": "끝", "id": "m1",
               "content_blocks": [{"contents": [tool]}]}

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body(
            {"event": "add_message", "data": {"sender": "User", "text": "안녕"}},
            {"event": "add_message", "data": message},
            {"event": "add_message", "data": message},  # 갱신 반복은 한 번만 보낸다
            {"event": "end", "data": {}},
        ))

    _, events = await run(tmp_path, handler)
    assert [type(e) for e in events].count(ToolStarted) == 1
    assert [type(e) for e in events].count(ToolFinished) == 1
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["끝"]


@pytest.mark.asyncio
async def test_http_error_becomes_provider_error(tmp_path: Path) -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(403, json={"detail": "Invalid API key"})

    with pytest.raises(ProviderError) as error:
        await run(tmp_path, handler)
    assert error.value.status == 403 and not error.value.retryable


@pytest.mark.asyncio
async def test_cancel_stops_turn(tmp_path: Path) -> None:
    cancel = asyncio.Event()
    cancel.set()

    async def stream() -> Any:
        await asyncio.sleep(5)
        yield b""

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=stream())

    result, _ = await run(tmp_path, handler, cancel)
    assert result.stop_reason == "cancelled"


@pytest.mark.asyncio
async def test_log_events_become_tool_events_and_suppress_content_blocks(tmp_path: Path) -> None:
    block = {"sender": "Machine", "text": "끝", "id": "m1", "content_blocks": [{"contents": [
        {"type": "tool_use", "name": "ls", "tool_input": {}, "output": "x"}]}]}

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body(
            {"event": "log", "data": {"name": "Tool Start", "message": {
                "type": "tool_start", "run_id": "r1", "serialized": {"name": "ls"},
                "input_str": "{'path': 'docs/adr'}"}}},
            {"event": "log", "data": {"name": "Tool End", "message": {
                "type": "tool_end", "run_id": "r1", "output": {"content": "a.md\n"}}}},
            {"event": "add_message", "data": block},
            {"event": "end", "data": {}},
        ))

    _, events = await run(tmp_path, handler)
    started = [e for e in events if isinstance(e, ToolStarted)]
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert len(started) == 1 and started[0].name == "ls"
    assert started[0].input == {"path": "docs/adr"}
    assert len(finished) == 1 and finished[0].output.content == "a.md\n"
