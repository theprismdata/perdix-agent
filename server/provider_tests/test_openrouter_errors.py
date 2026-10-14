from __future__ import annotations

from pathlib import Path

import httpx2
import pytest
from acp import RequestError
from acp.helpers import text_block

from perdix.acp.agent import PerdixAgent
from perdix.config.schema import ServerConfig
from perdix.context.view import ModelView
from perdix.llm.base import ModelRequest, ProviderError
from perdix.llm.openrouter import OpenRouterProvider
from perdix.memory.schema import MemoryConfig


@pytest.mark.asyncio
async def test_http_error_preserves_reason_and_status() -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(402, json={"error": {
            "code": 402, "message": "This request requires more credits, or fewer max_tokens.",
            "metadata": {"limit_source": "openrouter_key_limit"},
        }})

    provider = OpenRouterProvider("test/model", api_key="test-key",
                                  transport=httpx2.MockTransport(respond))
    request = ModelRequest(system="", view=ModelView(messages=[]),
                           tools=[], max_tokens=100)
    with pytest.raises(ProviderError) as error:
        async for _ in provider.stream(request):
            pass
    assert error.value.status == 402
    assert not error.value.retryable
    assert "fewer max_tokens" in str(error.value)
    assert "openrouter_key_limit" in str(error.value)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_error_detail_redacts_keys_and_ignores_raw_metadata(streaming: bool) -> None:
    api_key = "test-secret-token"
    payload = {"error": {
        "code": 402, "message": f"Rejected {api_key} Bearer private-token sk-or-v1-other-key",
        "metadata": {"provider_name": "test-provider", "raw": "do-not-display-private-body"},
    }}

    def respond(request: httpx2.Request) -> httpx2.Response:
        if streaming:
            import json

            return httpx2.Response(200, text="data: " + json.dumps(payload) + "\n\n")
        return httpx2.Response(402, json=payload)

    provider = OpenRouterProvider("test/model", api_key=api_key,
                                  transport=httpx2.MockTransport(respond))
    request = ModelRequest(system="", view=ModelView(messages=[]), max_tokens=100)
    with pytest.raises(ProviderError) as error:
        async for _ in provider.stream(request):
            pass
    detail = str(error.value)
    assert "Rejected" in detail
    assert "test-provider" in detail
    assert all(secret not in detail for secret in (
        api_key, "private-token", "sk-or-v1-other-key", "do-not-display-private-body",
    ))


@pytest.mark.parametrize("body", [b"<html>private proxy response</html>", b"invalid", b""])
@pytest.mark.asyncio
async def test_non_json_error_keeps_http_status_without_dumping_body(body: bytes) -> None:
    provider = OpenRouterProvider("test/model", api_key="test-key", transport=httpx2.MockTransport(
        lambda request: httpx2.Response(402, content=body),
    ))
    request = ModelRequest(system="", view=ModelView(messages=[]), max_tokens=100)
    with pytest.raises(ProviderError) as error:
        async for _ in provider.stream(request):
            pass
    assert error.value.status == 402
    assert "HTTP 402" in str(error.value)
    assert "private proxy response" not in str(error.value)


@pytest.mark.asyncio
async def test_error_preserves_retry_after_without_changing_retry_policy() -> None:
    provider = OpenRouterProvider("test/model", api_key="test-key", transport=httpx2.MockTransport(
        lambda request: httpx2.Response(402, headers={"Retry-After": "12"}, json={"error": {
            "code": 402, "message": "Concurrent request budget exceeded",
            "metadata": {"limit_source": "openrouter_in_flight_budget"},
        }}),
    ))
    request = ModelRequest(system="", view=ModelView(messages=[]), max_tokens=100)
    with pytest.raises(ProviderError) as error:
        async for _ in provider.stream(request):
            pass
    assert "Retry-After): 12" in str(error.value)
    assert not error.value.retryable


@pytest.mark.asyncio
async def test_http_detail_reaches_acp_error_response(tmp_path: Path) -> None:
    model = "test/model"
    agents = tmp_path / "agents.json"
    agents.write_text("{}", encoding="utf-8")
    agent = PerdixAgent(ServerConfig(
        provider="openrouter", model=model, db_path=str(tmp_path / "events.db"),
        memories=MemoryConfig(enabled=False, root=tmp_path / "memories"),
        credentials_path=tmp_path / "credentials.json", agents_config_path=agents,
    ))
    agent._providers[model] = OpenRouterProvider(model, api_key="test-key",
        transport=httpx2.MockTransport(lambda request: httpx2.Response(402, json={"error": {
            "code": 402, "message": "Requested max_tokens exceeds remaining credit budget",
            "metadata": {"limit_source": "openrouter_credits"},
        }})),
    )
    try:
        session = await agent.new_session(cwd=str(tmp_path))
        with pytest.raises(RequestError) as error:
            await agent.prompt(session.session_id, [text_block("hello")])
        assert "max_tokens exceeds" in str(error.value)
        assert "openrouter_credits" in str(error.value)
    finally:
        await agent.aclose()
