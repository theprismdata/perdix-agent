"""subagents/presets.py, subagents/spawn.py (architecture.md §6.8, §6.10)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from perdix.config.agents import AgentsConfig
from perdix.context.messages import TextBlock, UserMessage
from perdix.harness.budget import Budget
from perdix.harness.hooks import HookBus
from perdix.llm.fake import FakeProvider
from perdix.permissions.policy import PermissionPolicy
from perdix.sandbox.local import LocalBackend
from perdix.session.store import EventStore
from perdix.subagents.presets import get as get_preset
from perdix.subagents.spawn import spawn_subagent
from perdix.tools.registry import ToolRegistry, builtin_tools


def test_presets_match_architecture_table() -> None:
    explore = get_preset("explore")
    assert explore.model == "claude-haiku-4-5"
    assert explore.effort is None
    assert explore.mode.read_only is True

    executor = get_preset("executor")
    assert executor.model == "claude-opus-5-5"
    assert executor.effort == "high"
    assert executor.mode.read_only is False

    reviewer = get_preset("reviewer")
    assert reviewer.model == "claude-sonnet-5-5"
    assert reviewer.effort == "medium"
    assert reviewer.mode.read_only is True

    debugger = get_preset("debugger")
    assert debugger.model == "claude-sonnet-5-5"
    assert debugger.effort == "medium"
    assert debugger.mode.read_only is False

    security = get_preset("security-reviewer")
    assert security.model == "claude-sonnet-5-5"
    assert security.effort == "medium"
    assert security.mode.read_only is True

    with pytest.raises(KeyError):
        get_preset("not-a-preset")


def test_preset_mode_description_matches_role_description(tmp_path: Path) -> None:
    from perdix.context.builder import build_system_prompt
    from perdix.subagents.presets import all_presets

    for preset in all_presets():
        assert preset.mode.description == preset.description
    explore = get_preset("explore")
    assert explore.description in build_system_prompt(tmp_path, explore.mode)


@pytest.fixture
async def store() -> AsyncIterator[EventStore]:
    s = await EventStore.open(":memory:")
    yield s
    await s.close()


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    return root


async def test_spawn_subagent_uses_preset_model_and_is_isolated(
    store: EventStore, cwd: Path
) -> None:
    parent = await store.create_session(str(cwd), "build", "fake", "parent-model")
    await store.append(
        parent.id, "user_message", UserMessage(content=[TextBlock(text="parent task")])
    )

    providers: dict[str, FakeProvider] = {}

    def provider_factory(model: str) -> FakeProvider:
        provider = FakeProvider(
            [{"text": "explored the repo", "stop": "end_turn"}], model=model
        )
        providers[model] = provider
        return provider

    outcome = await spawn_subagent(
        "explore",
        "look around the repo",
        project_path=str(cwd),
        cwd=cwd,
        store=store,
        registry=ToolRegistry(builtin_tools()),
        policy=PermissionPolicy(),
        sandbox=LocalBackend(),
        budget=Budget(),
        hooks=HookBus(),
        provider_factory=provider_factory,
    )

    assert outcome.stop_reason == "end_turn"
    assert outcome.text == "explored the repo"
    assert outcome.session_id != parent.id
    assert "claude-haiku-4-5" in providers

    req = providers["claude-haiku-4-5"].requests[0]
    tool_names = {t.name for t in req.tools}
    assert "bash" not in tool_names and "write" not in tool_names and "edit" not in tool_names

    parent_events = await store.events(parent.id)
    assert len(parent_events) == 1  # 서브에이전트 실행이 부모 히스토리를 건드리지 않는다


async def test_spawn_subagent_executor_denies_unapproved_write_by_default(
    store: EventStore, cwd: Path
) -> None:
    def provider_factory(model: str) -> FakeProvider:
        return FakeProvider(
            [
                {
                    "tool_uses": [
                        {"id": "t1", "name": "write", "input": {"path": "x.txt", "content": "y"}}
                    ],
                    "stop": "tool_use",
                },
                {"text": "done anyway", "stop": "end_turn"},
            ],
            model=model,
        )

    outcome = await spawn_subagent(
        "executor",
        "write a file",
        project_path=str(cwd),
        cwd=cwd,
        store=store,
        registry=ToolRegistry(builtin_tools()),
        policy=PermissionPolicy(),
        sandbox=LocalBackend(),
        budget=Budget(),
        hooks=HookBus(),
        provider_factory=provider_factory,
    )

    assert outcome.stop_reason == "end_turn"
    assert outcome.text == "done anyway"
    assert not (cwd / "x.txt").exists()  # ask 판정은 승인 채널이 없어 기본 거부된다

    events = await store.events(outcome.session_id)
    results = [e for e in events if e.kind == "tool_result"]
    assert len(results) == 1
    assert results[0].payload["is_error"] is True


async def test_spawn_subagent_model_override(store: EventStore, cwd: Path) -> None:
    requested: list[str] = []

    def provider_factory(model: str) -> FakeProvider:
        requested.append(model)
        return FakeProvider([{"text": "ok", "stop": "end_turn"}], model=model)

    outcome = await spawn_subagent(
        "explore",
        "look around",
        project_path=str(cwd),
        cwd=cwd,
        store=store,
        registry=ToolRegistry(builtin_tools()),
        policy=PermissionPolicy(),
        sandbox=LocalBackend(),
        budget=Budget(),
        hooks=HookBus(),
        provider_factory=provider_factory,
        model="claude-sonnet-5-5",
    )

    assert outcome.stop_reason == "end_turn"
    assert requested == ["claude-sonnet-5-5"]  # 프리셋 기본(claude-haiku-4-5)이 아니라 override
    record = await store.get_session(outcome.session_id)
    assert record is not None and record.model == "claude-sonnet-5-5"


# ---------------------------------------------------------------- 역할별 모델 오버라이드 (§6.10)


def _agents_config(agents: dict[str, dict[str, str]]) -> AgentsConfig:
    return AgentsConfig.model_validate(
        {
            "models": {"cheap": {"provider": "anthropic", "model": "claude-haiku-4-5"}},
            "agents": agents,
        }
    )


def test_preset_override_replaces_model_and_effort() -> None:
    config = _agents_config({"explore": {"model": "claude-sonnet-5-5", "effort": "low"}})
    explore = get_preset("explore", config)
    assert explore.model == "claude-sonnet-5-5"
    assert explore.effort == "low"
    assert explore.mode.read_only is True  # 모드는 프리셋 그대로
    assert get_preset("explore").model == "claude-haiku-4-5"  # 기본 표는 바뀌지 않는다


def test_preset_override_resolves_alias_and_clears_effort() -> None:
    executor = get_preset("executor", _agents_config({"executor": {"model": "cheap"}}))
    assert executor.model == "claude-haiku-4-5"
    assert executor.effort is None  # effort 생략은 프리셋 effort 유지가 아니라 None


def test_preset_without_override_keeps_default() -> None:
    config = _agents_config({"executor": {"model": "cheap"}})
    assert get_preset("explore", config) == get_preset("explore")
    assert get_preset("explore", None).model == "claude-haiku-4-5"
    assert get_preset("reviewer", AgentsConfig()).effort == "medium"


def test_unknown_preset_raises_even_with_config() -> None:
    config = _agents_config({"not-a-preset": {"model": "cheap"}})
    with pytest.raises(KeyError):
        get_preset("not-a-preset", config)


async def test_spawn_subagent_uses_config_override(store: EventStore, cwd: Path) -> None:
    requested: list[str] = []

    def provider_factory(model: str) -> FakeProvider:
        requested.append(model)
        return FakeProvider([{"text": "ok", "stop": "end_turn"}], model=model)

    outcome = await spawn_subagent(
        "executor",
        "do it",
        project_path=str(cwd),
        cwd=cwd,
        store=store,
        registry=ToolRegistry(builtin_tools()),
        policy=PermissionPolicy(),
        sandbox=LocalBackend(),
        budget=Budget(),
        hooks=HookBus(),
        provider_factory=provider_factory,
        agents_config=_agents_config({"executor": {"model": "cheap"}}),
    )

    assert outcome.stop_reason == "end_turn"
    assert requested == ["claude-haiku-4-5"]  # 기본(claude-opus-5-5)이 아니라 설정값
    record = await store.get_session(outcome.session_id)
    assert record is not None and record.model == "claude-haiku-4-5"


async def test_spawn_subagent_explicit_model_beats_config(store: EventStore, cwd: Path) -> None:
    requested: list[str] = []

    def provider_factory(model: str) -> FakeProvider:
        requested.append(model)
        return FakeProvider([{"text": "ok", "stop": "end_turn"}], model=model)

    await spawn_subagent(
        "executor",
        "do it",
        project_path=str(cwd),
        cwd=cwd,
        store=store,
        registry=ToolRegistry(builtin_tools()),
        policy=PermissionPolicy(),
        sandbox=LocalBackend(),
        budget=Budget(),
        hooks=HookBus(),
        provider_factory=provider_factory,
        model="explicit-override",
        agents_config=_agents_config({"executor": {"model": "cheap"}}),
    )

    assert requested == ["explicit-override"]
