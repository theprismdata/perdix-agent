"""config/agents.py: 역할별 모델 오버라이드 설정 로딩·별칭 해석 (architecture.md §6.10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from perdix.__main__ import main
from perdix.config.agents import AgentRoleConfig, AgentsConfig, ModelAlias, load_agents_config


def test_empty_config_resolves_literal_model() -> None:
    config = AgentsConfig()
    assert config.models == {} and config.agents == {}
    assert config.resolve("claude-opus-5-5") == "claude-opus-5-5"


def test_resolve_alias_to_model_id() -> None:
    config = AgentsConfig.model_validate(
        {
            "models": {"cheap": {"provider": "anthropic", "model": "claude-haiku-4-5"}},
            "agents": {"explore": {"model": "cheap"}},
        }
    )
    assert config.resolve(config.agents["explore"].model) == "claude-haiku-4-5"
    assert config.resolve("cheap") == "claude-haiku-4-5"
    assert config.resolve("gpt-6.1-sol") == "gpt-6.1-sol"


def test_load_agents_config_none_is_empty() -> None:
    assert load_agents_config(None) == AgentsConfig()


def test_load_agents_config_from_file(tmp_path: Path) -> None:
    path = tmp_path / "agents.json"
    path.write_text(
        json.dumps(
            {
                "models": {
                    "strong": {"provider": "anthropic", "model": "claude-opus-5-5"},
                    "alt": {"provider": "openai", "model": "gpt-6.1-sol"},
                },
                "agents": {
                    "executor": {"model": "strong", "effort": "high"},
                    "explore": {"model": "alt"},
                },
            }
        )
    )
    config = load_agents_config(path)
    assert config.models["strong"] == ModelAlias(provider="anthropic", model="claude-opus-5-5")
    assert config.models["alt"].provider == "openai"
    assert config.agents["executor"] == AgentRoleConfig(model="strong", effort="high")
    assert config.agents["explore"].effort is None
    assert config.resolve("strong") == "claude-opus-5-5"


def test_cli_rejects_missing_agents_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.json"
    monkeypatch.setattr("sys.argv", ["perdix-server", "--agents-config", str(missing)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "agents config not found" in capsys.readouterr().err
