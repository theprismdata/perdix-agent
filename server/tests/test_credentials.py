"""config/credentials.py: 파일 로딩, env 우선순위, 권한 경고."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from perdix.config.credentials import Credentials, ProviderCredential, load_credentials


def write(path: Path, data: object, mode: int = 0o600) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(mode)
    return path


def test_missing_file_gives_empty(tmp_path: Path) -> None:
    creds = load_credentials(tmp_path / "nope.json")
    assert creds.providers == {}
    assert creds.api_key("anthropic", env={}) is None


def test_file_is_read_and_env_wins(tmp_path: Path) -> None:
    path = write(tmp_path / "credentials.json", {"anthropic": {"api_key": "file-key"}})
    creds = load_credentials(path)
    assert creds.api_key("anthropic", env={}) == "file-key"
    assert creds.api_key("anthropic", env={"ANTHROPIC_API_KEY": "env-key"}) == "env-key"
    assert creds.api_key("openai", env={}) is None


def test_short_form_and_base_url(tmp_path: Path) -> None:
    path = write(
        tmp_path / "c.json",
        {"anthropic": "sk-short", "openai": {"api_key": "sk-o", "base_url": "http://x"}},
    )
    creds = load_credentials(path)
    assert creds.providers["anthropic"] == ProviderCredential(api_key="sk-short")
    assert creds.providers["openai"].base_url == "http://x"


def test_broken_json_warns_and_is_empty(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")
    path.chmod(0o600)
    with caplog.at_level(logging.WARNING):
        creds = load_credentials(path)
    assert creds == Credentials()
    assert "unreadable" in caplog.text


def test_permissive_mode_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = write(tmp_path / "c.json", {"anthropic": {"api_key": "k"}}, mode=0o644)
    with caplog.at_level(logging.WARNING):
        load_credentials(path)
    assert "chmod 600" in caplog.text


def test_strict_mode_is_quiet(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = write(tmp_path / "c.json", {"anthropic": {"api_key": "k"}}, mode=0o600)
    with caplog.at_level(logging.WARNING):
        load_credentials(path)
    assert "chmod" not in caplog.text


def test_anthropic_provider_receives_file_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from perdix.llm.anthropic import AnthropicProvider

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    path = write(tmp_path / "c.json", {"anthropic": {"api_key": "sk-from-file"}})
    key = load_credentials(path).api_key("anthropic")
    provider = AnthropicProvider("claude-opus-5-5", api_key=key)
    assert provider.client.api_key == "sk-from-file"


def test_auth_token_is_read_and_env_wins(tmp_path: Path) -> None:
    path = write(tmp_path / "c.json", {"anthropic": {"auth_token": "oauth-file"}})
    creds = load_credentials(path)
    assert creds.auth_token("anthropic", env={}) == "oauth-file"
    assert creds.auth_token("anthropic", env={"ANTHROPIC_AUTH_TOKEN": "oauth-env"}) == "oauth-env"
    assert creds.api_key("anthropic", env={}) is None


def test_anthropic_provider_uses_bearer_oauth_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from perdix.llm.anthropic import OAUTH_BETA, AnthropicProvider

    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    path = write(tmp_path / "c.json", {"anthropic": {"auth_token": "oauth-xyz"}})
    token = load_credentials(path).auth_token("anthropic")
    provider = AnthropicProvider("claude-opus-5-5", auth_token=token)
    assert provider.client.auth_token == "oauth-xyz"
    assert provider.client.default_headers["anthropic-beta"] == OAUTH_BETA
