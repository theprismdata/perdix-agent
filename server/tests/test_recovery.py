"""도구 실행 중 서버 SIGKILL → 같은 DB로 session/load 복구.

m1.md §5 테스트 7, harness-loop.md §3.4.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, text_block
from acp.schema import (
    AllowedOutcome,
    PermissionOption,
    RequestPermissionResponse,
    ToolCallProgress,
    ToolCallUpdate,
)
from acp.stdio import spawn_agent_process

from perdix.context.view import find_unpaired_tool_uses
from perdix.session.store import EventStore

TIMEOUT = 30.0


class RecordingClient:
    def __init__(self) -> None:
        self.updates: list[Any] = []
        self.in_progress = asyncio.Event()

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append(update)
        if isinstance(update, ToolCallProgress) and update.status == "in_progress":
            self.in_progress.set()

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id="allow_once")
        )


def _server_args(script: Path, db: Path) -> list[str]:
    return [
        "-m",
        "perdix",
        "--provider",
        "fake",
        "--fake-script",
        str(script),
        "--db",
        str(db),
        "--permission-mode",
        "interactive",
        "--log-level",
        "WARNING",
    ]


async def test_load_session_recovers_interrupted_tool(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    db = tmp_path / "perdix.db"
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            [
                {
                    "text": "Sleeping.",
                    "tool_uses": [
                        {"id": "tu_sleep", "name": "bash", "input": {"command": "sleep 30"}}
                    ],
                    "stop": "tool_use",
                }
            ]
        ),
        encoding="utf-8",
    )

    # 1) 도구 실행 중에 서버를 SIGKILL
    first = RecordingClient()
    async with spawn_agent_process(
        first,
        sys.executable,
        *_server_args(script, db),
        transport_kwargs={"stderr": None},
        use_unstable_protocol=True,
    ) as (conn, process):
        await asyncio.wait_for(conn.initialize(protocol_version=PROTOCOL_VERSION), TIMEOUT)
        new = await asyncio.wait_for(
            conn.new_session(cwd=str(project), perdix={"mode": "build"}), TIMEOUT
        )
        sid = new.session_id
        prompt_task = asyncio.create_task(
            conn.prompt(session_id=sid, prompt=[text_block("sleep please")])
        )
        await asyncio.wait_for(first.in_progress.wait(), TIMEOUT)
        process.kill()
        await asyncio.wait_for(process.wait(), TIMEOUT)
        prompt_task.cancel()
        await asyncio.gather(prompt_task, return_exceptions=True)

    # 2) 같은 DB로 새 서버를 띄워 session/load
    second = RecordingClient()
    async with spawn_agent_process(
        second,
        sys.executable,
        *_server_args(script, db),
        transport_kwargs={"stderr": None},
        use_unstable_protocol=True,
    ) as (conn, _):
        init = await asyncio.wait_for(conn.initialize(protocol_version=PROTOCOL_VERSION), TIMEOUT)
        assert init.agent_capabilities is not None and init.agent_capabilities.load_session
        loaded = await asyncio.wait_for(
            conn.load_session(cwd=str(project), session_id=sid), TIMEOUT
        )
        assert loaded.modes is not None and loaded.modes.current_mode_id == "build"

    kinds = [u.session_update for u in second.updates]
    assert "user_message_chunk" in kinds
    assert "agent_message_chunk" in kinds
    calls = [u for u in second.updates if u.session_update == "tool_call"]
    assert [c.tool_call_id for c in calls] == ["tu_sleep"]
    assert calls[0].kind == "execute"
    failed = [
        u for u in second.updates if u.session_update == "tool_call_update" and u.status == "failed"
    ]
    assert [u.tool_call_id for u in failed] == ["tu_sleep"]
    assert failed[0].content is not None
    assert any("interrupted" in c.content.text for c in failed[0].content)

    store = await EventStore.open(db)
    try:
        events = await store.events(sid)
    finally:
        await store.close()
    results = [e.as_tool_result() for e in events if e.kind == "tool_result"]
    assert [(r.tool_use_id, r.is_error) for r in results] == [("tu_sleep", True)]
    assert results[0].content == "interrupted: process exited"
    assert find_unpaired_tool_uses(events) == []
