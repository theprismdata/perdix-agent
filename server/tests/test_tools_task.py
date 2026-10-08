"""tools/task.py와 TurnRunner의 서브에이전트 스폰 배선 (harness-loop.md §8)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from perdix.config.agents import AgentsConfig
from perdix.context.builder import build_system_prompt
from perdix.context.messages import TextBlock
from perdix.harness.budget import Budget
from perdix.harness.events import HarnessEvent, PermissionOutcome, PermissionRequest
from perdix.harness.hooks import HookBus
from perdix.harness.modes import BUILD
from perdix.harness.turn import TurnRunner
from perdix.llm.fake import FakeProvider
from perdix.permissions.policy import PermissionPolicy
from perdix.sandbox.local import LocalBackend
from perdix.session.store import EventStore
from perdix.subagents.spawn import SubagentOutcome
from perdix.tools.base import ToolContext
from perdix.tools.registry import READ_ONLY_KINDS, ToolRegistry, builtin_tools
from perdix.tools.task import TaskTool


def _ctx(tmp_path: Path, spawn: object = None) -> ToolContext:
    return ToolContext(cwd=tmp_path, sandbox=LocalBackend(), spawn_subagent=spawn)  # type: ignore[arg-type]


def test_task_read_only_depends_on_preset() -> None:
    tool = TaskTool()
    assert tool.is_read_only({"preset": "executor", "prompt": "x"}) is False
    assert tool.is_read_only({"preset": "explore", "prompt": "x"}) is True
    assert tool.is_read_only({"preset": "reviewer", "prompt": "x"}) is True
    assert tool.is_read_only({"preset": "debugger", "prompt": "x"}) is False
    assert tool.is_read_only({"preset": "security-reviewer", "prompt": "x"}) is True
    assert tool.kind not in READ_ONLY_KINDS


def test_task_is_builtin_but_excluded_from_read_only_registry() -> None:
    registry = ToolRegistry(builtin_tools())
    assert "task" in registry.names()
    assert "task" not in registry.read_only_only().names()


def test_task_schema_hides_internal_presets() -> None:
    enum = TaskTool().input_schema["properties"]["preset"]["enum"]
    assert set(enum) == {"explore", "executor", "reviewer", "debugger", "security-reviewer"}


async def test_task_without_spawn_is_error(tmp_path: Path) -> None:
    out = await TaskTool().run(_ctx(tmp_path), {"preset": "explore", "prompt": "look"})
    assert out.is_error is True


async def test_task_returns_subagent_text(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str | None]] = []

    async def spawn(preset: str, prompt: str, model: str | None) -> SubagentOutcome:
        calls.append((preset, prompt, model))
        return SubagentOutcome(session_id="child", stop_reason="end_turn", text="found it")

    out = await TaskTool().run(
        _ctx(tmp_path, spawn),
        {"preset": "reviewer", "prompt": "review", "model": "claude-haiku-4-5"},
    )
    assert out.is_error is False
    assert out.content == "found it"
    assert calls == [("reviewer", "review", "claude-haiku-4-5")]


async def test_task_early_stop_is_error(tmp_path: Path) -> None:
    async def spawn(preset: str, prompt: str, model: str | None) -> SubagentOutcome:
        return SubagentOutcome(session_id="child", stop_reason="max_turn_requests", text="partial")

    out = await TaskTool().run(_ctx(tmp_path, spawn), {"preset": "executor", "prompt": "do"})
    assert out.is_error is True
    assert "max_turn_requests" in out.content
    assert "partial" in out.content


async def test_task_unknown_preset_is_error(tmp_path: Path) -> None:
    async def spawn(preset: str, prompt: str, model: str | None) -> SubagentOutcome:
        raise KeyError(f"unknown preset: {preset}")

    out = await TaskTool().run(_ctx(tmp_path, spawn), {"preset": "bogus", "prompt": "do"})
    assert out.is_error is True
    assert "bogus" in out.content


# ---------------------------------------------------------------- TurnRunner 통합


@pytest.fixture
async def store() -> AsyncIterator[EventStore]:
    s = await EventStore.open(":memory:")
    yield s
    await s.close()


async def _ignore(_: HarnessEvent) -> None:
    return None


async def _reject(_: PermissionRequest) -> PermissionOutcome:
    return PermissionOutcome(outcome="reject_once")


async def test_turn_runner_task_spawns_child_and_returns_text(
    store: EventStore, tmp_path: Path
) -> None:
    parent = await store.create_session(str(tmp_path), "build", "fake", "parent-model")
    parent_provider = FakeProvider(
        [
            {
                "tool_uses": [
                    {
                        "id": "t1",
                        "name": "task",
                        "input": {"preset": "explore", "prompt": "find the entry point"},
                    }
                ],
                "stop": "tool_use",
            },
            {"text": "parent done", "stop": "end_turn"},
        ],
        model="parent-model",
    )
    children: dict[str, FakeProvider] = {}

    def provider_factory(model: str) -> FakeProvider:
        child = FakeProvider([{"text": "entry is main.py", "stop": "end_turn"}], model=model)
        children[model] = child
        return child

    runner = TurnRunner(
        store=store,
        provider=parent_provider,
        registry=ToolRegistry(builtin_tools()),
        policy=PermissionPolicy({"task": "allow"}),
        sandbox=LocalBackend(),
        budget=Budget(),
        hooks=HookBus(),
        mode=BUILD,
        cwd=tmp_path,
        system_prompt_builder=build_system_prompt,
        model="parent-model",
        max_tokens=1000,
        provider_factory=provider_factory,
    )
    result = await runner.run(
        parent.id,
        [TextBlock(text="go")],
        cancel=asyncio.Event(),
        sink=_ignore,
        ask_permission=_reject,
    )

    assert result.stop_reason == "end_turn"
    assert list(children) == ["claude-haiku-4-5"]
    child_tools = {t.name for t in children["claude-haiku-4-5"].requests[0].tools}
    assert "task" not in child_tools  # 읽기 전용 서브에이전트는 재귀 스폰 불가

    results = [e for e in await store.events(parent.id) if e.kind == "tool_result"]
    assert len(results) == 1
    assert results[0].payload["is_error"] is False
    assert "entry is main.py" in str(results[0].payload)


async def test_turn_runner_task_uses_agents_config_override(
    store: EventStore, tmp_path: Path
) -> None:
    parent = await store.create_session(str(tmp_path), "build", "fake", "parent-model")
    parent_provider = FakeProvider(
        [
            {
                "tool_uses": [
                    {
                        "id": "t1",
                        "name": "task",
                        "input": {"preset": "explore", "prompt": "find the entry point"},
                    }
                ],
                "stop": "tool_use",
            },
            {"text": "parent done", "stop": "end_turn"},
        ],
        model="parent-model",
    )
    children: dict[str, FakeProvider] = {}

    def provider_factory(model: str) -> FakeProvider:
        child = FakeProvider([{"text": "entry is main.py", "stop": "end_turn"}], model=model)
        children[model] = child
        return child

    agents_config = AgentsConfig.model_validate(
        {
            "models": {"mid": {"provider": "anthropic", "model": "claude-sonnet-5-5"}},
            "agents": {"explore": {"model": "mid"}},
        }
    )
    runner = TurnRunner(
        store=store,
        provider=parent_provider,
        registry=ToolRegistry(builtin_tools()),
        policy=PermissionPolicy({"task": "allow"}),
        sandbox=LocalBackend(),
        budget=Budget(),
        hooks=HookBus(),
        mode=BUILD,
        cwd=tmp_path,
        system_prompt_builder=build_system_prompt,
        model="parent-model",
        max_tokens=1000,
        provider_factory=provider_factory,
        agents_config=agents_config,
    )
    result = await runner.run(
        parent.id,
        [TextBlock(text="go")],
        cancel=asyncio.Event(),
        sink=_ignore,
        ask_permission=_reject,
    )

    assert result.stop_reason == "end_turn"
    assert list(children) == ["claude-sonnet-5-5"]  # 기본(claude-haiku-4-5)이 아니라 설정값
    child_sessions = [s for s in await store.list_sessions() if s.id != parent.id]
    assert [s.model for s in child_sessions] == ["claude-sonnet-5-5"]
