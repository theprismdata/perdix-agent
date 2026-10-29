import asyncio
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from perdix.gateway.config import AcpAgentConfig, GatewayConfig, ProjectConfig, TelegramConfig
from perdix.gateway.events import GatewayStore
from perdix.gateway.runtime import GatewayRuntime, SubmitRequest

FIXTURE = Path(__file__).parent / "fixtures" / "external_acp.py"


@pytest.mark.parametrize("command", [[], [""], ["hermes", "\x00"], ["  "]])
def test_invalid_commands_are_rejected(command: list[str]) -> None:
    # Given / When / Then: 운영자 설정 경계에서 잘못된 실행 명령을 거절한다.
    with pytest.raises(ValidationError):
        AcpAgentConfig(command=command)


@pytest.mark.asyncio
@pytest.mark.parametrize("supports_load", [True, False])
async def test_external_acp_routing_and_session_reuse(tmp_path: Path, supports_load: bool,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: 별도 프로세스의 서로 다른 ACP 서버와 채널 비밀이 있다.
    monkeypatch.setenv("PERDIX_GATEWAY_TOKEN", "gateway-secret")
    monkeypatch.setenv("PERDIX_TELEGRAM_TOKEN", "bot-secret")
    monkeypatch.setenv("CUSTOM_BOT_SECRET", "custom-secret")
    config = GatewayConfig(
        db_path=tmp_path / "gateway.db",
        telegram=TelegramConfig(token_env="CUSTOM_BOT_SECRET",
                                allowed_user_ids=[123], allowed_chat_ids=[123]),
        projects={name: ProjectConfig(cwd=tmp_path, acp=AcpAgentConfig(command=(
            sys.executable, str(FIXTURE), "load" if supports_load else "fresh", name,
        ))) for name in ("hermes", "openclaw")},
    )
    runtime = GatewayRuntime(config, GatewayStore(config.db_path))
    await runtime.start()
    try:
        # When: 같은 채팅에서 서버를 전환하고 첫 서버에 다시 질문한다.
        replies = []
        for index, name in enumerate(("hermes", "openclaw", "hermes")):
            job = await runtime.submit(SubmitRequest(project=name, prompt="hello"),
                                       "telegram", f"telegram:123:123:{index}")
            async with asyncio.timeout(15):
                await runtime.tasks[job.id]
            reply = await runtime.store.get(job.id)
            assert reply is not None
            replies.append(reply)
        # Then: 대상별로 응답하고 지원할 때만 세션을 복구하며 비밀을 격리한다.
        assert all(reply.status == "completed" for reply in replies)
        payloads = [json.loads(reply.result) for reply in replies]
        assert [p["agent"] for p in payloads] == ["hermes", "openclaw", "hermes"]
        assert [p["count"] for p in payloads] == [1, 1, 2 if supports_load else 1]
        assert not any(p["secrets_present"] for p in payloads)
        assert replies[0].agent != replies[1].agent
        assert (replies[0].session_id == replies[2].session_id) == supports_load
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_missing_external_executable_reports_failed_job(tmp_path: Path) -> None:
    # Given: 설치되지 않은 외부 실행 파일이 등록되어 있다.
    config = GatewayConfig(db_path=tmp_path / "gateway.db", projects={
        "external": ProjectConfig(cwd=tmp_path, acp=AcpAgentConfig(command=(
            str(tmp_path / "missing-agent"), "acp",
        ))),
    })
    runtime = GatewayRuntime(config, GatewayStore(config.db_path))
    await runtime.start()
    try:
        # When: 해당 프로젝트에 요청한다.
        job = await runtime.submit(SubmitRequest(project="external", prompt="hello"))
        async with asyncio.timeout(15):
            await runtime.tasks[job.id]
        # Then: 다른 서버로 우회하지 않고 실패 상태를 영속 기록한다.
        result = await runtime.store.get(job.id)
        assert result is not None and result.status == "failed"
        assert result.session_id is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_changed_backend_does_not_receive_previous_session(tmp_path: Path) -> None:
    # Given: 같은 프로젝트 이름에 서로 다른 에이전트 실행 설정을 순서대로 적용한다.
    sessions = []
    for index, label in enumerate(("hermes", "openclaw")):
        config = GatewayConfig(db_path=tmp_path / "gateway.db", projects={
            "main": ProjectConfig(cwd=tmp_path, acp=AcpAgentConfig(command=(
                sys.executable, str(FIXTURE), "load", label,
            ))),
        })
        runtime = GatewayRuntime(config, GatewayStore(config.db_path))
        await runtime.start()
        try:
            # When: 재시작 이후 동일 사용자와 채팅에서 후속 요청을 보낸다.
            job = await runtime.submit(SubmitRequest(project="main", prompt="hello"),
                                       "telegram", f"telegram:123:123:{index}")
            async with asyncio.timeout(15):
                await runtime.tasks[job.id]
            result = await runtime.store.get(job.id)
            assert result is not None and result.status == "completed"
            sessions.append(result.session_id)
            # Then: 이전 에이전트의 세션 파일을 복구하지 않는다.
            assert json.loads(result.result)["count"] == 1
        finally:
            await runtime.close()
    assert sessions[0] != sessions[1]
