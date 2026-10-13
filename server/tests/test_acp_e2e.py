"""ACP e2e: 서버를 하위 프로세스로 띄우고 SDK 클라이언트로 stdio 왕복 (m1.md §5 테스트 6)."""

from __future__ import annotations

import asyncio
import json
import shlex
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

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
from pydantic import SecretStr

from perdix.llm.openai_credentials import OAuthRecord, write_record
from perdix.acp.agent import PerdixAgent
from perdix.config.schema import ServerConfig
from perdix.llm.fake import FakeProvider
from perdix.memory.schema import MemoryConfig

TIMEOUT = 30.0


@pytest.mark.parametrize("target", ["plan", "build"])
@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini", "openrouter"])
async def test_assignment_changes_provider_and_effort_in_current_turn(
    tmp_path: Path, target: str, provider: str,
) -> None:
    config_path = tmp_path / "agents.json"
    config_path.write_text(json.dumps({
        "models": {
            "cheap": {"provider": "openai", "model": "gpt-6-luna"},
            "strong": {"provider": provider, "model": "custom-assigned-model"},
        },
        "agents": {
            "chat": {"model": "cheap", "effort": "low"},
            target: {"model": "strong", "effort": "high"},
        },
    }))
    chat = FakeProvider([{"tool_uses": [{
        "id": "switch", "name": "switch_mode", "input": {"mode": target},
    }]}], name="openai", model="gpt-6-luna")
    assigned = FakeProvider([{"text": "assigned model answered"}], name=provider,
                            model="custom-assigned-model")
    agent = PerdixAgent(ServerConfig(
        agents_config_path=config_path, db_path=str(tmp_path / "events.db"),
        credentials_path=tmp_path / "absent.json", memories=MemoryConfig(enabled=False),
    ))
    agent._providers.update({"gpt-6-luna": chat, "custom-assigned-model": assigned})
    try:
        session = await agent.new_session(cwd=str(tmp_path))
        assert session.config_options[0].current_value == "gpt-6-luna"
        assert agent._provider_name_for("custom-assigned-model") == provider
        response = await agent.prompt(session.session_id, [text_block("작업해줘")])
        assert response.stop_reason == "end_turn"
        assert len(chat.requests) == 1
        assert chat.requests[0].effort == "low"
        assert len(assigned.requests) == 1
        assert assigned.requests[0].effort == "high"
        store = await agent._get_store()
        record = await store.get_session(session.session_id)
        assert record is not None and record.model == "custom-assigned-model"
        assert record.provider == provider
        restored = await agent.load_session(cwd=str(tmp_path), session_id=session.session_id)
        assert restored.config_options[0].current_value == "custom-assigned-model"
    finally:
        await agent.aclose()


@pytest.mark.parametrize("target", ["build", "plan", "chat"])
async def test_model_switches_mode_over_stdio(tmp_path: Path, target: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    script = [
        {"tool_uses": [{"id": "switch", "name": "switch_mode", "input": {"mode": target}}]},
    ]
    if target == "build":
        script.append({"tool_uses": [{
            "id": "write", "name": "write", "input": {"path": "hello.txt", "content": "hi"},
        }]})
    else:
        script.append({"tool_uses": [{
            "id": "write", "name": "write", "input": {"path": "blocked.txt", "content": "x"},
        }]})
    script.append({"text": "done"})
    client = RecordingClient()
    async with spawn_server(client, script, tmp_path, tmp_path / "perdix.db") as (conn, _):
        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        session = await conn.new_session(cwd=str(project))
        if target == "chat":
            await conn.set_session_mode(session_id=session.session_id, mode_id="build")
        requests = {
            "build": "hello.txt 파일을 만들어줘",
            "plan": "구현하지 말고 계획만 세워줘",
            "chat": "코드 동작을 설명해줘",
        }
        response = await asyncio.wait_for(conn.prompt(
            session_id=session.session_id, prompt=[text_block(requests[target])],
        ), TIMEOUT)
        assert response.stop_reason == "end_turn"
        assert client.of_type("current_mode_update")[-1].current_mode_id == target
        if target == "build":
            assert (project / "hello.txt").read_text() == "hi"
            assert [req[0].tool_call_id for req in client.permission_requests] == ["write"]
        else:
            assert not client.permission_requests
            assert not (project / "blocked.txt").exists()


async def test_switch_and_write_in_one_batch_are_rejected(tmp_path: Path) -> None:
    script = [{"tool_uses": [
        {"id": "switch", "name": "switch_mode", "input": {"mode": "build"}},
        {"id": "write", "name": "write", "input": {"path": "unsafe.txt", "content": "x"}},
    ]}, {"text": "done"}]
    client = RecordingClient()
    async with spawn_server(client, script, tmp_path, tmp_path / "perdix.db") as (conn, _):
        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        session = await conn.new_session(cwd=str(tmp_path))
        await conn.prompt(session_id=session.session_id, prompt=[text_block("파일 생성해줘")])
        assert not (tmp_path / "unsafe.txt").exists()
        assert not client.of_type("current_mode_update")


async def test_automatic_build_preserves_deny_policy(tmp_path: Path) -> None:
    script = [
        {"tool_uses": [{"id": "switch", "name": "switch_mode", "input": {"mode": "build"}}]},
        {"tool_uses": [{
            "id": "write", "name": "write", "input": {"path": "denied.txt", "content": "x"},
        }]},
        {"text": "done"},
    ]
    client = RecordingClient()
    async with spawn_server(
        client, script, tmp_path, tmp_path / "perdix.db", permission_mode="deny",
    ) as (conn, _):
        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        session = await conn.new_session(cwd=str(tmp_path))
        await conn.prompt(session_id=session.session_id, prompt=[text_block("파일 만들어줘")])
        assert client.of_type("current_mode_update")[-1].current_mode_id == "build"
        assert not (tmp_path / "denied.txt").exists()
        assert not client.permission_requests


async def test_chatgpt_authenticate_over_stdio(tmp_path: Path) -> None:
    credentials = tmp_path / "credentials.json"
    credentials.write_text('{"openai":{"api_key":""}}')
    write_record(tmp_path / "openai-oauth.json", OAuthRecord(
        client_id="oaiapp_test", subject="user-test", ext_agent_host_id="urn:uuid:test",
        access_token=SecretStr("test-access"), refresh_token=SecretStr("test-refresh"),
        scopes=("chatgpt.tokens.use.direct",), expires_at=time.time() + 3600,
    ))
    async with spawn_agent_process(
        RecordingClient(), sys.executable, "-m", "perdix", "--credentials", str(credentials),
        "--db", str(tmp_path / "auth.db"), use_unstable_protocol=True,
    ) as (conn, _):
        initialized = await asyncio.wait_for(
            conn.initialize(protocol_version=PROTOCOL_VERSION), TIMEOUT,
        )
        assert initialized.auth_methods is not None
        assert {method.id for method in initialized.auth_methods} == {"openai-chatgpt"}
        result = await asyncio.wait_for(conn.authenticate(method_id="openai-chatgpt"), TIMEOUT)
        assert result is not None


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
    client: RecordingClient,
    script: list[dict[str, Any]],
    tmp: Path,
    db: Path,
    *,
    cwd: Path | None = None,
    trust_project: bool = False,
    permission_mode: str = "interactive",
) -> AsyncIterator[tuple[Any, Any]]:
    script_path = tmp / f"script-{time.monotonic_ns()}.json"
    script_path.write_text(json.dumps(script), encoding="utf-8")
    agents_path = tmp / "test-agents.json"
    agents_path.write_text("{}", encoding="utf-8")
    args = [
        "-m",
        "perdix",
        "--provider",
        "fake",
        "--fake-script",
        str(script_path),
        "--agents-config",
        str(agents_path),
        "--db",
        str(db),
        "--permission-mode",
        permission_mode,
        "--log-level",
        "WARNING",
    ]
    if trust_project:
        args.append("--trust-project")
    async with spawn_agent_process(
        client,
        sys.executable,
        *args,
        cwd=cwd,
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


async def test_loop_repairs_real_file_over_stdio(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".perdix").mkdir(parents=True)
    (project / "answer").write_text("wrong")
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        "from pathlib import Path; assert Path('answer').read_text() == '42'"
    )
    (project / ".perdix/config.json").write_text(
        json.dumps(
            {
                "verify": {"commands": [{"name": "test", "run": command}]},
            }
        )
    )
    script = [
        {"text": "done"},
        {
            "tool_uses": [
                {"id": "fix", "name": "write", "input": {"path": "answer", "content": "42"}}
            ]
        },
        {"text": "done"},
    ]
    client = RecordingClient()
    async with spawn_server(
        client, script, tmp_path, tmp_path / "loop.db", cwd=project, trust_project=True
    ) as (conn, _):
        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        sid = (await conn.new_session(cwd=str(project))).session_id
        commands = client.of_type("available_commands_update")
        assert commands and commands[-1].available_commands[0].name == "loop"
        result = await asyncio.wait_for(
            conn.prompt(session_id=sid, prompt=[text_block("/loop fix answer")]), TIMEOUT
        )
        assert result.field_meta == {"perdix": {"verification": "passed"}}
        assert (project / "answer").read_text() == "42"
        checks = [u for u in client.of_type("tool_call") if u.title == "verify: test"]
        assert len(checks) == 2
        assert len(client.permission_requests) == 3


async def test_loop_cancel_status_and_resume_after_restart(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".perdix").mkdir(parents=True)
    command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
        "from pathlib import Path; assert Path('answer').read_text() == '42'"
    )
    (project / ".perdix/config.json").write_text(
        json.dumps(
            {
                "verify": {"commands": [{"name": "test", "run": command}]},
            }
        )
    )
    sleep = f"{shlex.quote(sys.executable)} -c " + shlex.quote("import time; time.sleep(30)")
    client = RecordingClient()
    db = tmp_path / "resume.db"
    async with spawn_server(
        client,
        [{"tool_uses": [{"id": "sleep", "name": "bash", "input": {"command": sleep}}]}],
        tmp_path,
        db,
        cwd=project,
        trust_project=True,
    ) as (conn, _):
        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        sid = (await conn.new_session(cwd=str(project))).session_id
        pending = asyncio.create_task(
            conn.prompt(session_id=sid, prompt=[text_block("/loop fix answer")])
        )
        await asyncio.wait_for(client.in_progress.wait(), TIMEOUT)
        status = await conn.prompt(session_id=sid, prompt=[text_block("/loop status")])
        assert status.stop_reason == "end_turn"
        assert not pending.done()
        await conn.prompt(session_id=sid, prompt=[text_block("/loop cancel")])
        cancelled = await asyncio.wait_for(pending, TIMEOUT)
        assert cancelled.stop_reason == "cancelled"
        assert cancelled.field_meta == {"perdix": {"verification": "failed"}}
    resumed_client = RecordingClient()
    script = [
        {
            "tool_uses": [
                {"id": "fix", "name": "write", "input": {"path": "answer", "content": "42"}}
            ]
        },
        {"text": "done"},
    ]
    async with spawn_server(
        resumed_client, script, tmp_path, db, cwd=project, trust_project=True
    ) as (conn, _):
        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        await conn.load_session(session_id=sid, cwd=str(project))
        result = await asyncio.wait_for(
            conn.prompt(session_id=sid, prompt=[text_block("/loop resume")]), TIMEOUT
        )
        assert result.field_meta == {"perdix": {"verification": "passed"}}
        assert (project / "answer").read_text() == "42"
