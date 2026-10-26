from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from acp import RequestError, text_block

from perdix.acp.agent import PerdixAgent
from perdix.config.agents import AgentRoleConfig, AgentsConfig, ModelAlias
from perdix.config.auth_commands import AuthCommand, run_profile_command
from perdix.config.auth_profiles import (
    PROVIDERS,
    AuthProfile,
    AuthProfileError,
    AuthProfiles,
    AuthProvider,
)
from perdix.config.schema import ServerConfig
from perdix.llm.base import ProviderError
from perdix.llm.openai import OpenAIProvider
from perdix.llm.openai_credentials import OAuthRecord, oauth_path, write_record
from perdix.llm.profile_provider import profile_provider
from perdix.memory.schema import MemoryConfig


async def add(profiles: AuthProfiles, provider: AuthProvider, name: str) -> None:
    await run_profile_command(profiles, AuthCommand(
        action="login", provider=provider, profile=name, api_key="test-" + name,
    ))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_named_key_ignores_environment_and_logout_blocks_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: AuthProvider,
) -> None:
    profiles = AuthProfiles(tmp_path / "credentials.json")
    monkeypatch.setenv(provider.upper() + "_API_KEY", "wrong-environment-account")
    await add(profiles, provider, "work")

    selected = profile_provider(profiles, "test-model", provider, "work")

    assert selected.name == provider
    assert selected._api_key == "test-work"
    profile = profiles.read().get_profile(provider, "work")
    assert profile is not None
    if os.name == "posix":
        assert profiles.credential_path(profile).stat().st_mode & 0o777 == 0o600
    await run_profile_command(profiles, AuthCommand(
        action="logout", provider=provider, profile="work",
    ))
    with pytest.raises(ProviderError, match="재인증"):
        profile_provider(profiles, "test-model", provider, "work")
    assert "test-work" not in await run_profile_command(profiles, AuthCommand(action="list"))


@pytest.mark.asyncio
async def test_profile_identity_cannot_change_and_other_keys_survive(tmp_path: Path) -> None:
    profiles = AuthProfiles(tmp_path / "credentials.json")
    await add(profiles, "openai", "personal")
    await add(profiles, "gemini", "work")
    before = profiles.read()

    with pytest.raises(AuthProfileError, match="바꿀 수 없습니다"):
        await add(profiles, "openrouter", "personal")

    assert profiles.read() == before
    assert profiles.ready(before.profiles["personal"])
    assert profiles.ready(before.profiles["work"])


@pytest.mark.parametrize("name", ["../outside", "/tmp/outside", "legacy", "", "a/b"])
def test_profile_name_rejects_paths_and_reserved_name(name: str) -> None:
    with pytest.raises(ValueError):
        AuthProfile(name=name, provider="openai", method="api_key")


def test_corrupt_registry_fails_without_falling_back(tmp_path: Path) -> None:
    profiles = AuthProfiles(tmp_path / "credentials.json")
    profiles.path.write_text('{"profiles":{"work":"secret-value"}}')

    with pytest.raises(AuthProfileError) as failure:
        profiles.defaults()

    assert "secret-value" not in str(failure.value)


@pytest.mark.asyncio
async def test_session_keeps_each_provider_selection_after_defaults_change_and_restart(
    tmp_path: Path,
) -> None:
    profiles = AuthProfiles(tmp_path / "credentials.json")
    for provider in PROVIDERS:
        await add(profiles, provider, provider + "-work")
        profiles.use(provider, provider + "-work")
    (tmp_path / "agents.json").write_text("{}")
    config = ServerConfig(db_path=str(tmp_path / "sessions.db"),
                          credentials_path=profiles.credentials_path,
                          agents_config_path=tmp_path / "agents.json",
                          memories=MemoryConfig(enabled=False))
    agent = PerdixAgent(config)
    session = await agent.new_session(str(tmp_path))
    session_id = session.session_id
    try:
        for provider in PROVIDERS:
            profiles.use(provider, "legacy")
        await agent.set_config_option("auth_profile_openai", session_id, "legacy")
        store = await agent._get_store()
        events_before = await store.events(session_id)
        assert all("test-" not in json.dumps(event.payload) for event in events_before)
    finally:
        await agent.aclose()
    restarted = PerdixAgent(config)

    try:
        loaded = await restarted.load_session(str(tmp_path), session_id)

        assert loaded is not None
        options = {option.id: option.current_value for option in loaded.config_options or []}
        for provider in PROVIDERS:
            expected = "legacy" if provider == "openai" else provider + "-work"
            assert options["auth_profile_" + provider] == expected
        assert await restarted.prompt(session_id, [text_block("/auth status")])
        async with restarted._runner.guard(session_id):
            with pytest.raises(RequestError):
                await restarted.set_config_option("auth_profile_gemini", session_id, "legacy")
    finally:
        await restarted.aclose()


@pytest.mark.asyncio
async def test_role_profile_and_memory_use_session_account(tmp_path: Path) -> None:
    profiles = AuthProfiles(tmp_path / "credentials.json")
    await add(profiles, "openai", "personal")
    await add(profiles, "openai", "work")
    profiles.use("openai", "personal")
    agents = tmp_path / "agents.json"
    agents.write_text(AgentsConfig(
        models={"test": ModelAlias(provider="openai", model="test-openai")},
        agents={"reviewer": AgentRoleConfig(model="test", auth_profile="work")},
    ).model_dump_json())
    agent = PerdixAgent(ServerConfig(db_path=str(tmp_path / "sessions.db"),
        credentials_path=profiles.credentials_path, agents_config_path=agents,
        memories=MemoryConfig(enabled=False)))
    try:
        session_id = (await agent.new_session(str(tmp_path))).session_id

        selected = agent._provider_for("test-openai", session_id=session_id, role_id="reviewer")

        assert isinstance(selected, OpenAIProvider)
        assert selected._api_key == "test-work"
        profiles.use("openai", "work")
        events = await (await agent._get_store()).events(session_id)
        memory = agent._memory_provider_for("test-openai", events)
        assert isinstance(memory, OpenAIProvider)
        assert memory._api_key == "test-personal"
        with pytest.raises(ProviderError, match="프로필"):
            agent._provider_for("claude-opus-5-5", session_id=session_id, role_id="reviewer")
    finally:
        await agent.aclose()


def test_server_profile_commands_hide_key_and_preserve_legacy(tmp_path: Path) -> None:
    credentials = tmp_path / "credentials.json"
    credentials.write_text('{"anthropic":{"api_key":"legacy-key"}}')
    args = [sys.executable, "-m", "perdix", "--credentials", str(credentials),
            "--auth-provider", "openrouter", "--auth-profile", "work"]

    result = subprocess.run([*args, "--auth-command", "login", "--auth-api-key-stdin"],
                            input="test-private-key", text=True, capture_output=True, check=True)

    assert "test-private-key" not in result.stdout + result.stderr
    listed = subprocess.run([*args, "--auth-command", "list"],
                            text=True, capture_output=True, check=True)
    assert "work" in listed.stdout
    assert "test-private-key" not in listed.stdout
    assert json.loads(credentials.read_text())["anthropic"]["api_key"] == "legacy-key"


def test_oauth_profiles_have_independent_token_paths_and_ignore_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = AuthProfiles(tmp_path / "credentials.json")
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-account")
    for name in ("personal", "work"):
        profile = AuthProfile(name=name, provider="openai", method="oauth")
        profiles.save(profile)
        write_record(oauth_path(profiles.credential_path(profile)), OAuthRecord(
            client_id=name, subject=name, ext_agent_host_id=name, refresh_token="refresh-" + name,
        ))

    personal = profile_provider(profiles, "test-model", "openai", "personal")
    work = profile_provider(profiles, "test-model", "openai", "work")

    assert isinstance(personal, OpenAIProvider) and isinstance(work, OpenAIProvider)
    assert personal._api_key is None and work._api_key is None
    assert not personal._use_environment and not work._use_environment
    assert personal._credentials_path != work._credentials_path
    assert "work" in str(work._credentials_path)
