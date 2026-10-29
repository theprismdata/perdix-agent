from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import httpx2
import openai
import pytest
from acp import PROTOCOL_VERSION
from acp.schema import ClientCapabilities
from acp.stdio import spawn_agent_process

from perdix.config.auth_commands import AuthCommand, run_profile_command
from perdix.config.auth_profiles import PROVIDERS, AuthProfile, AuthProfiles, AuthProvider
from perdix.config.schema import ServerConfig
from perdix.context.view import ModelView
from perdix.gateway.config import GatewayConfig, ProjectConfig
from perdix.gateway.events import GatewayStore
from perdix.gateway.runtime import GatewayRuntime, JobClient, SubmitRequest
from perdix.llm.anthropic import AnthropicProvider
from perdix.llm.base import ModelRequest, ProviderError
from perdix.llm.gemini import GeminiProvider
from perdix.llm.openai import OpenAIProvider
from perdix.llm.openai_credentials import OAuthRecord, oauth_path, write_record
from perdix.llm.openai_oauth import DIRECT_SCOPE
from perdix.llm.openrouter import OpenRouterProvider
from perdix.llm.profile_provider import profile_provider
from perdix.sandbox.base import scrub_env


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_selected_key_reaches_real_adapter_http_headers(
    tmp_path: Path, provider: AuthProvider, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = AuthProfiles(tmp_path / "credentials.json")
    await run_profile_command(profiles, AuthCommand(
        action="login", provider=provider, profile="work", api_key="test-work",
    ))
    monkeypatch.setenv(provider.upper() + "_API_KEY", "wrong-account")
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(401, json={"error": {"message": "unauthorized",
                                                   "type": "authentication_error"}})

    transport = httpx2.MockTransport(respond)
    selected = profile_provider(profiles, "test-model", provider, "work")
    sdk = None
    if isinstance(selected, (AnthropicProvider, OpenAIProvider)):
        sdk = selected.client
        await sdk._client.aclose()
        sdk._client = httpx2.AsyncClient(transport=transport)
    elif isinstance(selected, (GeminiProvider, OpenRouterProvider)):
        selected._transport = transport
    try:
        with pytest.raises(ProviderError):
            async for _ in selected.stream(ModelRequest(
                system="", view=ModelView(messages=[]), max_tokens=16,
            )):
                pass
    finally:
        if sdk is not None:
            await sdk.close()

    assert len(requests) == 1
    header = "x-api-key" if provider == "anthropic" else (
        "x-goog-api-key" if provider == "gemini" else "authorization")
    expected = "test-work" if provider in ("anthropic", "gemini") else "Bearer test-work"
    assert requests[0].headers[header] == expected


@pytest.mark.asyncio
async def test_chatgpt_profile_sends_own_cached_token_despite_environment_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = AuthProfiles(tmp_path / "credentials.json")
    profile = AuthProfile(name="work", provider="openai", method="oauth")
    profiles.save(profile)
    write_record(oauth_path(profiles.credential_path(profile)), OAuthRecord(
        client_id="work", subject="work", ext_agent_host_id="work",
        refresh_token="refresh-work", access_token="token-work", scopes=(DIRECT_SCOPE,),
        expires_at=time.time() + 3600,
    ))
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-account")
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(401, json={"error": {"message": "unauthorized"}})

    original = openai.AsyncOpenAI

    def client(*, api_key: str, base_url: str | None, max_retries: int) -> openai.AsyncOpenAI:
        return original(api_key=api_key, base_url=base_url, max_retries=max_retries,
                        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)))

    monkeypatch.setattr(openai, "AsyncOpenAI", client)
    selected = profile_provider(profiles, "gpt-6.1-sol", "openai", "work")

    with pytest.raises(ProviderError):
        async for _ in selected.stream(ModelRequest(
            system="", view=ModelView(messages=[]), max_tokens=16,
        )):
            pass

    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer token-work"


@pytest.mark.asyncio
async def test_standard_acp_stdio_selects_and_restores_profiles_without_model_call(
    tmp_path: Path,
) -> None:
    profiles = AuthProfiles(tmp_path / "credentials.json")
    for name in ("work", "personal"):
        await run_profile_command(profiles, AuthCommand(
            action="login", provider="openai", profile=name, api_key="test-" + name,
        ))
    profiles.use("openai", "personal")
    agents = tmp_path / "agents.json"
    agents.write_text("{}")
    config = GatewayConfig(db_path=tmp_path / "gateway.db",
        projects={"test": ProjectConfig(cwd=tmp_path)},
        server=ServerConfig(db_path=str(tmp_path / "sessions.db"),
            credentials_path=profiles.credentials_path, agents_config_path=agents))
    runtime = GatewayRuntime(config, GatewayStore(config.db_path))
    await runtime.start()
    try:
        job = await runtime.submit(SubmitRequest(project="test", prompt="/auth status"))
        task = runtime.tasks[job.id]

        await asyncio.wait_for(task, timeout=20)

        completed = await runtime.store.get(job.id)
        assert completed is not None and completed.status == "completed"
        assert completed.result is not None and "personal" in completed.result
        assert "test-personal" not in completed.result
        session_id = completed.session_id
        assert session_id is not None
        async with spawn_agent_process(
            JobClient(runtime, job.id), sys.executable, "-m", "perdix",
            "--db", str(tmp_path / "sessions.db"), "--credentials", str(profiles.credentials_path),
            "--agents-config", str(agents), env=scrub_env(os.environ), cwd=tmp_path,
        ) as (conn, _):
            await conn.initialize(protocol_version=PROTOCOL_VERSION,
                                  client_capabilities=ClientCapabilities())
            initial = await conn.load_session(
                cwd=str(tmp_path), session_id=session_id, mcp_servers=[],
            )
            assert initial is not None
            response = await conn.set_config_option(
                session_id=session_id, config_id="auth_profile_openai", value="work",
            )
            assert response is not None
            choices = {option.id: option.current_value for option in response.config_options}
            assert choices["auth_profile_openai"] == "work"
            assert all("auth_profile_" + provider in choices for provider in PROVIDERS)
            loaded = await conn.load_session(
                cwd=str(tmp_path), session_id=session_id, mcp_servers=[],
            )
            assert loaded is not None
            restored = {option.id: option.current_value for option in loaded.config_options or []}
            assert restored["auth_profile_openai"] == "work"
    finally:
        await runtime.close()
