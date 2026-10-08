"""ACP e2e: 서버를 하위 프로세스로 띄우고 SDK 클라이언트로 stdio 왕복 (m1.md §5 테스트 6)."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, text_block
from acp.schema import (
    AllowedOutcome,
    ConfigOptionUpdate,
    PermissionOption,
    RequestPermissionResponse,
    ToolCallProgress,
    ToolCallUpdate,
)
from acp.stdio import spawn_agent_process

TIMEOUT = 30.0


class RecordingClient:
    """session/update를 기록하고 request_permission에 allow_once로 답하는 최소 ACP 클라이언트."""

    def __init__(self) -> None:
        self.updates: list[Any] = []
        self.permission_requests: list[tuple[ToolCallUpdate, list[PermissionOption]]] = []
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
        self.permission_requests.append((tool_call, options))
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id="allow_once")
        )

    def of_type(self, session_update: str) -> list[Any]:
        return [u for u in self.updates if u.session_update == session_update]


@asynccontextmanager
async def spawn_server(
    client: RecordingClient, script: list[dict[str, Any]], tmp: Path, db: Path
) -> AsyncIterator[tuple[Any, Any]]:
    script_path = tmp / f"script-{time.monotonic_ns()}.json"
    script_path.write_text(json.dumps(script), encoding="utf-8")
    args = [
        "-m",
        "perdix",
        "--provider",
        "fake",
        "--fake-script",
        str(script_path),
        "--db",
        str(db),
        "--permission-mode",
        "interactive",
        "--log-level",
        "WARNING",
    ]
    async with spawn_agent_process(
        client,
        sys.executable,
        *args,
        transport_kwargs={"stderr": None},
        use_unstable_protocol=True,
    ) as (conn, process):
        yield conn, process


async def test_full_turn_with_permission(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    script = [
        {
            "text": "Writing the file.",
            "tool_uses": [
                {
                    "id": "tu_write",
                    "name": "write",
                    "input": {"path": "hello.txt", "content": "hi\n"},
                }
            ],
            "stop": "tool_use",
        },
        {"text": "done", "stop": "end_turn"},
    ]
    client = RecordingClient()
    async with spawn_server(client, script, tmp_path, tmp_path / "perdix.db") as (conn, _):
        init = await asyncio.wait_for(conn.initialize(protocol_version=PROTOCOL_VERSION), TIMEOUT)
        assert init.agent_capabilities is not None
        assert init.agent_capabilities.load_session is True
        assert init.agent_capabilities.session_capabilities is not None
        assert init.agent_capabilities.session_capabilities.list is not None

        new = await asyncio.wait_for(conn.new_session(cwd=str(project)), TIMEOUT)
        assert new.modes is not None
        assert new.modes.current_mode_id == "chat"
        assert {m.id for m in new.modes.available_modes} == {"chat", "plan", "build"}
        assert new.config_options is not None
        model_opt = new.config_options[0]
        assert model_opt.id == "model"
        assert model_opt.category == "model"
        sid = new.session_id

        await asyncio.wait_for(conn.set_session_mode(session_id=sid, mode_id="build"), TIMEOUT)
        assert any(u.current_mode_id == "build" for u in client.of_type("current_mode_update"))

        resp = await asyncio.wait_for(
            conn.prompt(session_id=sid, prompt=[text_block("create hello.txt")]), TIMEOUT
        )
        assert resp.stop_reason == "end_turn"
        assert resp.field_meta == {"perdix": {"verification": "skipped"}}

        assert client.of_type("agent_message_chunk")
        calls = client.of_type("tool_call")
        assert len(calls) == 1
        assert calls[0].tool_call_id == "tu_write"
        assert calls[0].kind == "edit"
        assert calls[0].status == "pending"
        completed = [u for u in client.of_type("tool_call_update") if u.status == "completed"]
        assert len(completed) == 1
        assert completed[0].content is not None
        diffs = [c for c in completed[0].content if c.type == "diff"]
        assert len(diffs) == 1
        # ACP Diff.path는 절대 경로여야 한다
        assert diffs[0].path == str((project / "hello.txt").resolve())
        assert client.of_type("usage_update")
        assert len(client.permission_requests) == 1
        tool_call, options = client.permission_requests[0]
        assert tool_call.tool_call_id == "tu_write"
        assert {o.kind for o in options} == {
            "allow_once",
            "allow_always",
            "reject_once",
            "reject_always",
        }
        assert (project / "hello.txt").read_text(encoding="utf-8") == "hi\n"

        listed = await asyncio.wait_for(conn.list_sessions(cwd=str(project)), TIMEOUT)
        assert sid in {s.session_id for s in listed.sessions}

        result = await asyncio.wait_for(
            conn.set_config_option(config_id="model", session_id=sid, value="claude-haiku-4-5"),
            TIMEOUT,
        )
        assert result.config_options[0].current_value == "claude-haiku-4-5"
        updates = [u for u in client.updates if isinstance(u, ConfigOptionUpdate)]
        assert updates
        assert updates[-1].config_options[0].current_value == "claude-haiku-4-5"


async def test_cancel_during_long_bash(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    script = [
        {
            "tool_uses": [{"id": "tu_sleep", "name": "bash", "input": {"command": "sleep 30"}}],
            "stop": "tool_use",
        },
    ]
    client = RecordingClient()
    async with spawn_server(client, script, tmp_path, tmp_path / "perdix.db") as (conn, _):
        await asyncio.wait_for(conn.initialize(protocol_version=PROTOCOL_VERSION), TIMEOUT)
        new = await asyncio.wait_for(
            conn.new_session(cwd=str(project), perdix={"mode": "build"}), TIMEOUT
        )
        assert new.modes is not None and new.modes.current_mode_id == "build"
        sid = new.session_id

        prompt_task = asyncio.create_task(
            conn.prompt(session_id=sid, prompt=[text_block("sleep please")])
        )
        await asyncio.wait_for(client.in_progress.wait(), TIMEOUT)
        started = time.monotonic()
        await conn.cancel(session_id=sid)
        resp = await asyncio.wait_for(prompt_task, 10.0)
        assert resp.stop_reason == "cancelled"
        assert time.monotonic() - started < 5.0
        failed = [u for u in client.of_type("tool_call_update") if u.status == "failed"]
        assert [u.tool_call_id for u in failed] == ["tu_sleep"]
