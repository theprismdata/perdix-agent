"""웹 일반 대화가 conversationId로 ACP 세션을 이어 가는지 실제 HTTP 경로로 검증한다."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest
import pytest_asyncio
from pydantic import SecretStr

from perdix.config.schema import ServerConfig
from perdix.gateway.app import create_app
from perdix.gateway.config import GatewayConfig, GatewaySecrets, ProjectConfig
from perdix.gateway.events import Job
from perdix.gateway.runtime import GatewayRuntime

TOKEN = "test-gateway-token-with-enough-characters"


@pytest_asyncio.fixture
async def gateway(tmp_path: Path) -> AsyncIterator[tuple[httpx2.AsyncClient, GatewayRuntime]]:
    script = tmp_path / "responses.json"
    script.write_text('[{"text": "안녕!"}]')
    config = GatewayConfig(
        db_path=tmp_path / "gateway.db",
        projects={"main": ProjectConfig(cwd=tmp_path)},
        server=ServerConfig(provider="fake", fake_script=script,
                            db_path=str(tmp_path / "sessions.db"),
                            credentials_path=tmp_path / "credentials.json",
                            team_db=tmp_path / "teams.db"),
    )
    app = create_app(config, GatewaySecrets(gateway=SecretStr(TOKEN)))
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver",
            headers={"Authorization": "Bearer " + TOKEN},
        ) as client:
            yield client, app.state.runtime


async def turn(client: httpx2.AsyncClient, runtime: GatewayRuntime,
               conversation: str | None) -> Job:
    body: dict[str, str] = {"project": "main", "prompt": "안녕"}
    if conversation:
        body["conversationId"] = conversation
    response = await client.post("/api/jobs", json=body)
    assert response.status_code == 202
    job_id = response.json()["id"]
    task = runtime.tasks.get(job_id)
    if task is not None:
        async with asyncio.timeout(30):
            await task
    job = await runtime.store.get(job_id)
    assert job is not None and job.status == "completed"
    return job


@pytest.mark.asyncio
async def test_same_conversation_reuses_session(gateway) -> None:
    # Given: 같은 대화 ID로 두 턴, 다른 대화 ID로 한 턴을 보낸다.
    client, runtime = gateway
    first = await turn(client, runtime, "chat-aaaaaaaa")
    second = await turn(client, runtime, "chat-aaaaaaaa")
    other = await turn(client, runtime, "chat-bbbbbbbb")
    # Then: 같은 대화는 같은 세션을 쓰고, 다른 대화는 새 세션을 받는다.
    assert first.id != second.id
    assert first.session_id is not None
    assert second.session_id == first.session_id
    assert other.session_id not in (None, first.session_id)
    assert first.source == "web"
    assert first.source_id is not None and first.source_id.startswith("web-chat:chat-aaaaaaaa:")


@pytest.mark.asyncio
async def test_plain_jobs_do_not_share_sessions(gateway) -> None:
    # Given: 대화 ID 없는 기존 대시보드 작업이다.
    client, runtime = gateway
    first = await turn(client, runtime, None)
    second = await turn(client, runtime, None)
    # Then: 기존처럼 작업마다 새 세션이다.
    assert first.source_id is None
    assert first.session_id != second.session_id


@pytest.mark.asyncio
@pytest.mark.parametrize("conversation", ["telegram:1:1", "short", "a" * 65])
async def test_invalid_conversation_id_is_rejected(gateway, conversation: str) -> None:
    # Given: 접두 위조나 형식 위반 대화 ID다.
    client, _ = gateway
    response = await client.post("/api/jobs", json={
        "project": "main", "prompt": "x", "conversationId": conversation,
    })
    # Then: 작업을 만들지 않는다.
    assert response.status_code == 422
