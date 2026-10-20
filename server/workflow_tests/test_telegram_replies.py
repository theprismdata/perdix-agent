import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import httpx2
import pytest
from pydantic import SecretStr

from perdix.config.schema import ServerConfig
from perdix.gateway.channels import GatewayChannels, TelegramUpdate
from perdix.gateway.config import GatewayConfig, GatewaySecrets, ProjectConfig, TelegramConfig
from perdix.gateway.events import GatewayStore, Job
from perdix.gateway.runtime import GatewayRuntime, SubmitRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["/run perdix 너 이름이 뭐냐?", "너 이름이 뭐냐?"])
async def test_run_sends_only_the_final_answer(tmp_path: Path, text: str) -> None:
    config = GatewayConfig(
        db_path=tmp_path / "gateway.db",
        projects={"perdix": ProjectConfig(cwd=tmp_path)},
        telegram=TelegramConfig(allowed_user_ids=[123], allowed_chat_ids=[123]),
    )
    runtime = GatewayRuntime(config, GatewayStore(config.db_path))
    job = Job(id="job-id", project="perdix", prompt="너 이름이 뭐냐?", source="telegram",
              source_id="telegram:123:123:1", created_at=1)
    runtime.submit = AsyncMock(return_value=job)
    sent: list[str] = []

    def telegram(request: httpx2.Request) -> httpx2.Response:
        import json

        sent.append(json.loads(request.content)["text"])
        return httpx2.Response(200, json={"ok": True})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(telegram)) as http:
        channels = GatewayChannels(runtime, GatewaySecrets(
            gateway=SecretStr("test-gateway-token-long-enough"), telegram=SecretStr("test-token"),
        ), http)
        await channels.handle_telegram(TelegramUpdate.model_validate({
            "update_id": 1,
            "message": {"chat": {"id": 123}, "from": {"id": 123},
                        "text": text},
        }))
        assert sent == []
        runtime.submit.assert_awaited_once()
        assert runtime.submit.call_args.args[0].prompt == "너 이름이 뭐냐?"
        await channels.finished(job.model_copy(update={
            "status": "completed", "result": "나는 Perdix야. 편하게 불러줘.",
        }))
        assert sent == ["나는 Perdix야. 편하게 불러줘."]


@pytest.mark.asyncio
async def test_telegram_followup_reuses_conversation(tmp_path: Path) -> None:
    script = tmp_path / "responses.json"
    script.write_text('[{"text": "안녕!"}]')
    config = GatewayConfig(
        db_path=tmp_path / "gateway.db",
        projects={"perdix": ProjectConfig(cwd=tmp_path)},
        server=ServerConfig(provider="fake", fake_script=script,
                            db_path=str(tmp_path / "sessions.db"),
                            credentials_path=tmp_path / "credentials.json",
                            team_db=tmp_path / "teams.db"),
    )
    runtime = GatewayRuntime(config, GatewayStore(config.db_path))
    await runtime.start()
    try:
        first = await runtime.submit(SubmitRequest(project="perdix", prompt="내 이름은 홍중이야"),
                                     "telegram", "telegram:123:123:1")
        async with asyncio.timeout(30):
            await runtime.tasks[first.id]
        followup = await runtime.submit(SubmitRequest(project="perdix", prompt="내 이름 기억해?"),
                                        "telegram", "telegram:123:123:2")
        async with asyncio.timeout(30):
            await runtime.tasks[followup.id]
        first_result = await runtime.store.get(first.id)
        second_result = await runtime.store.get(followup.id)
        assert first_result is not None and second_result is not None
        assert first_result.status == second_result.status == "completed"
        assert first_result.session_id is not None
        assert second_result.session_id == first_result.session_id
    finally:
        await runtime.close()
