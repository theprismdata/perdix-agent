from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from acp import RequestError, text_block

from perdix.acp.agent import PerdixAgent, stricter_permission_mode
from perdix.config.schema import ServerConfig
from perdix.gateway.config import GatewayConfig, ProjectConfig
from perdix.gateway.events import GatewayStore
from perdix.gateway.runtime import GatewayRuntime, SubmitRequest
from perdix.permissions.policy import PermissionMode, PermissionPolicy


@pytest.mark.parametrize("tool", ["edit", "write", "bash", "memory_append", "mcp:test"])
def test_yolo_allows_tools_despite_deny_rules(tool: str) -> None:
    # Given: 설정과 세션 모두 거부한 도구.
    policy = PermissionPolicy({tool: "deny"}, mode="yolo")
    policy.remember(tool, {"command": "echo test"}, "deny")
    # When: 전체 승인 모드에서 실행 권한을 판정한다.
    decision = policy.evaluate(tool, {"command": "echo test"})
    # Then: 재승인 없이 허용한다.
    assert decision == "allow"


@pytest.mark.parametrize("base", ["interactive", "deny", "accept-edits", "allowlist"])
def test_session_metadata_cannot_enable_yolo(base: PermissionMode) -> None:
    # Given: 서버가 전체 승인을 허용하지 않았다.
    # When: 세션이 더 느슨한 권한을 요청한다.
    result = stricter_permission_mode(base, "yolo")
    # Then: 서버의 권한 경계를 유지한다.
    assert result == base


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_mode", ["interactive", "yolo"])
async def test_stdio_yolo_writes_and_executes_without_approval(
    tmp_path: Path, initial_mode: PermissionMode,
) -> None:
    # Given: 실제 서버가 쓰기와 서로 다른 명령 두 개를 실행한다.
    script = tmp_path / "fake.json"
    script.write_text(json.dumps([
        {"tool_uses": [{"id": "write", "name": "write",
                         "input": {"path": "written.txt", "content": "written"}}]},
        {"tool_uses": [{"id": "shell1", "name": "bash",
                         "input": {"command": "printf first > first.txt"}}]},
        {"tool_uses": [{"id": "shell2", "name": "bash",
                         "input": {"command": "printf second > second.txt"}}]},
        {"text": "done", "stop": "end_turn"},
    ]))
    config = GatewayConfig(
        db_path=tmp_path / "gateway.db",
        projects={"test": ProjectConfig(cwd=tmp_path)},
        server=ServerConfig(provider="fake", fake_script=script, permission_mode=initial_mode,
                            db_path=str(tmp_path / "sessions.db"),
                            credentials_path=tmp_path / "credentials.json"),
    )
    runtime = GatewayRuntime(config, GatewayStore(config.db_path))
    await runtime.start()
    try:
        # When: 승인 응답기를 두지 않고 stdio로 프롬프트를 전송한다.
        job = await runtime.submit(SubmitRequest(project="test", prompt="create files"))
        task = runtime.tasks[job.id]
        async with asyncio.timeout(30):
            while not task.done():
                for permission_id, pending in list(runtime.permissions.items()):
                    if not pending.future.done():
                        assert any(option.option_id == "yolo" for option in pending.options)
                        runtime.approve(job.id, permission_id, "yolo")
                await asyncio.sleep(0.01)
            await task
        events = await runtime.store.events(job.id)
        # Then: 실제 파일이 생성되고 승인 요청 이벤트가 없다.
        assert (tmp_path / "written.txt").read_text() == "written"
        assert (tmp_path / "first.txt").read_text() == "first"
        assert (tmp_path / "second.txt").read_text() == "second"
        assert sum(event.kind == "permission" for event in events) == (
            1 if initial_mode == "interactive" else 0
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_runtime_mode_selection_restores_and_can_be_disabled(tmp_path: Path) -> None:
    config = ServerConfig(db_path=str(tmp_path / "sessions.db"),
                          credentials_path=tmp_path / "credentials.json")
    agent = PerdixAgent(config)
    try:
        session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
        await agent.set_config_option("permission_mode", session.session_id, "yolo")
        await agent.load_session(cwd=str(tmp_path), session_id=session.session_id, mcp_servers=[])
        policy = agent._state(session.session_id).policy
        assert policy.evaluate("bash", {"command": "one"}) == "allow"
        await agent.prompt(session.session_id, [text_block("/permissions interactive")])
        assert policy.evaluate("bash", {"command": "two"}) == "ask"
        async with agent._runner.guard(session.session_id):
            with pytest.raises(RequestError):
                await agent.set_config_option("permission_mode", session.session_id, "yolo")
        assert policy.evaluate("write", {"path": "test.txt"}) == "ask"
    finally:
        await agent.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["deny", "accept-edits", "allowlist"])
async def test_runtime_selection_cannot_loosen_restricted_server(
    tmp_path: Path, mode: PermissionMode,
) -> None:
    agent = PerdixAgent(ServerConfig(permission_mode=mode,
        db_path=str(tmp_path / "sessions.db"), credentials_path=tmp_path / "credentials.json"))
    try:
        session = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
        with pytest.raises(RequestError):
            await agent.set_config_option("permission_mode", session.session_id, "yolo")
        assert agent._state(session.session_id).permission_mode == mode
    finally:
        await agent.aclose()
