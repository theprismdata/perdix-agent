from __future__ import annotations

import asyncio
import json
import shlex
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from perdix.acp.agent import PerdixAgent
from perdix.config.project import ProjectConfig, load_project
from perdix.config.schema import ServerConfig
from perdix.context.builder import build_system_prompt
from perdix.context.messages import UserMessage
from perdix.harness.budget import Budget
from perdix.harness.events import HarnessEvent, PermissionOutcome, PermissionRequest, ToolRunning
from perdix.harness.goal import GoalRunner
from perdix.harness.goal_commands import LoopCommands, latest_loop
from perdix.harness.goal_state import LoopState
from perdix.harness.hooks import HookBus
from perdix.harness.modes import BUILD
from perdix.harness.turn import TurnRunner
from perdix.harness.verifier import LoopCallbacks
from perdix.llm.fake import FakeProvider
from perdix.permissions.policy import PermissionPolicy
from perdix.sandbox.local import LocalBackend
from perdix.session.store import EventStore
from perdix.tools.registry import ToolRegistry, builtin_tools


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[EventStore]:
    db = await EventStore.open(tmp_path / "events.db")
    yield db
    await db.close()


def configure(cwd: Path, command: str, **limits: int | float) -> ProjectConfig:
    path = cwd / ".perdix" / "config.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "verify": {"commands": [{"name": "check", "run": command, "timeout": 10}]},
                "loop": limits,
            }
        )
    )
    return load_project(cwd)[0]


def python_command(code: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


async def allow(_: PermissionRequest) -> PermissionOutcome:
    return PermissionOutcome(outcome="allow_once")


async def ignore(_: HarnessEvent) -> None:
    return None


async def setup(
    store: EventStore, cwd: Path, responses: list[dict[str, Any]]
) -> tuple[GoalRunner, LoopState, FakeProvider]:
    sid = (await store.create_session(str(cwd), "build", "fake", "fake")).id
    prepared = await LoopCommands(store, cwd.resolve()).prepare(sid, "파일 수정", cwd)
    assert isinstance(prepared, LoopState)
    provider = FakeProvider(responses)
    turn = TurnRunner(
        store=store,
        provider=provider,
        registry=ToolRegistry(builtin_tools()),
        policy=PermissionPolicy(),
        sandbox=LocalBackend(),
        budget=Budget(),
        hooks=HookBus(),
        mode=BUILD,
        cwd=cwd,
        system_prompt_builder=build_system_prompt,
        model="fake",
    )
    return GoalRunner(turn, LoopCallbacks(sid, asyncio.Event(), ignore, allow)), prepared, provider


async def test_repairs_after_real_verification_failure(store: EventStore, tmp_path: Path) -> None:
    configure(
        tmp_path,
        python_command("from pathlib import Path; assert Path('answer').read_text() == '42'"),
    )
    (tmp_path / "answer").write_text("wrong")
    runner, state, provider = await setup(
        store,
        tmp_path,
        [
            {"text": "done"},
            {
                "tool_uses": [
                    {"id": "fix", "name": "write", "input": {"path": "answer", "content": "42"}}
                ]
            },
            {"text": "done"},
        ],
    )
    result = await runner.run(state)
    final = await latest_loop(store, runner.callbacks.session_id)
    assert result.verification == "passed"
    assert (tmp_path / "answer").read_text() == "42"
    assert final is not None and final.iteration == 2 and final.steps_used == 3
    assert final.status == "passed"
    feedback = [m for m in provider.requests[1].view.messages if isinstance(m, UserMessage)]
    assert "AssertionError" in feedback[-1].text()
    assert feedback[-1].origin == "synthetic"


@pytest.mark.parametrize(
    ("limits", "reason", "attempts"),
    [
        ({"max_iterations": 2}, "iteration_limit", 2),
        ({"max_steps": 1}, "step_limit", 1),
        ({"max_stagnant_iterations": 2}, "no_progress", 2),
    ],
)
async def test_stops_without_false_success(
    store: EventStore, tmp_path: Path, limits: dict[str, int], reason: str, attempts: int
) -> None:
    configure(tmp_path, python_command("raise SystemExit(1)"), **limits)
    runner, state, provider = await setup(store, tmp_path, [{"text": "done"}] * 5)
    result = await runner.run(state)
    final = await latest_loop(store, runner.callbacks.session_id)
    assert result.verification == "failed"
    assert final is not None and final.reason == reason and final.status == "stopped"
    assert len(provider.requests) == attempts


async def test_denied_verification_never_runs(store: EventStore, tmp_path: Path) -> None:
    configure(tmp_path, python_command("from pathlib import Path; Path('marker').touch()"))
    runner, state, _ = await setup(store, tmp_path, [{"text": "done"}])
    runner.turn.policy = PermissionPolicy(mode="deny")
    result = await runner.run(state)
    final = await latest_loop(store, runner.callbacks.session_id)
    assert result.verification == "failed"
    assert final is not None and final.reason == "permission_denied"
    assert not (tmp_path / "marker").exists()


async def test_permission_denial_stops_before_next_model_call(
    store: EventStore, tmp_path: Path
) -> None:
    configure(tmp_path, python_command("pass"))
    runner, state, provider = await setup(
        store,
        tmp_path,
        [
            {
                "tool_uses": [{"id": "deny", "name": "bash", "input": {"command": "exit 0"}}],
            }
        ],
    )
    runner.turn.policy = PermissionPolicy(mode="deny")
    result = await runner.run(state)
    final = await latest_loop(store, runner.callbacks.session_id)
    assert result.verification == "failed"
    assert final is not None and final.reason == "permission_denied"
    assert len(provider.requests) == 1


async def test_changed_config_is_not_executed(store: EventStore, tmp_path: Path) -> None:
    configure(tmp_path, python_command("from pathlib import Path; Path('marker').touch()"))
    runner, state, _ = await setup(
        store,
        tmp_path,
        [
            {
                "tool_uses": [
                    {
                        "id": "config",
                        "name": "write",
                        "input": {
                            "path": ".perdix/config.json",
                            "content": "{}",
                        },
                    }
                ],
            },
            {"text": "done"},
        ],
    )
    result = await runner.run(state)
    final = await latest_loop(store, runner.callbacks.session_id)
    assert result.verification == "failed"
    assert final is not None and final.reason == "config_changed"
    assert not (tmp_path / "marker").exists()


async def test_cancel_in_verification_and_resume_keeps_budget(
    store: EventStore, tmp_path: Path
) -> None:
    configure(tmp_path, python_command("import time; time.sleep(30)"))
    runner, state, _ = await setup(store, tmp_path, [{"text": "done"}, {"text": "done"}])

    async def cancel_when_running(event: HarnessEvent) -> None:
        if isinstance(event, ToolRunning):
            runner.callbacks.cancel.set()

    callbacks = LoopCallbacks(
        runner.callbacks.session_id, runner.callbacks.cancel, cancel_when_running, allow
    )
    result = await GoalRunner(runner.turn, callbacks).run(state)
    final = await latest_loop(store, runner.callbacks.session_id)
    assert result.stop_reason == "cancelled"
    assert final is not None and final.reason == "cancelled" and final.steps_used == 1
    resumed = await LoopCommands(store, tmp_path.resolve()).prepare(
        callbacks.session_id, "resume", tmp_path
    )
    assert isinstance(resumed, LoopState) and resumed.id == state.id and resumed.steps_used == 1
    assert resumed.iteration == 1


async def test_time_limit_cleans_running_tool(store: EventStore, tmp_path: Path) -> None:
    configure(tmp_path, python_command("pass"), timeout=0.1)
    runner, state, _ = await setup(
        store,
        tmp_path,
        [
            {
                "tool_uses": [
                    {
                        "id": "sleep",
                        "name": "bash",
                        "input": {"command": python_command("import time; time.sleep(30)")},
                    }
                ]
            }
        ],
    )
    result = await runner.run(state)
    final = await latest_loop(store, runner.callbacks.session_id)
    assert result.verification == "failed"
    assert final is not None and final.reason == "time_limit"
    assert runner.callbacks.cancel.is_set()


async def test_shared_budget_includes_child_agent(store: EventStore, tmp_path: Path) -> None:
    configure(tmp_path, python_command("pass"), max_steps=2)
    runner, state, provider = await setup(
        store,
        tmp_path,
        [
            {
                "tool_uses": [
                    {
                        "id": "child",
                        "name": "task",
                        "input": {
                            "preset": "explore",
                            "prompt": "read project",
                        },
                    }
                ]
            },
            {"text": "child done"},
        ],
    )
    runner.turn.policy = PermissionPolicy({"task": "allow"})
    runner.turn.provider_factory = lambda _: provider
    result = await runner.run(state)
    final = await latest_loop(store, runner.callbacks.session_id)
    assert result.verification == "failed"
    assert final is not None and final.reason == "step_limit" and final.steps_used == 2
    assert len(provider.requests) == 2


async def test_untrusted_project_never_prepares_loop(store: EventStore, tmp_path: Path) -> None:
    configure(tmp_path, python_command("pass"))
    sid = (await store.create_session(str(tmp_path), "chat", "fake", "fake")).id
    result = await LoopCommands(store, None).prepare(sid, "goal", tmp_path)
    assert isinstance(result, str)
    assert await latest_loop(store, sid) is None


async def test_child_permission_denial_blocks_parent_success(
    store: EventStore, tmp_path: Path
) -> None:
    configure(tmp_path, python_command("pass"))
    runner, state, provider = await setup(
        store,
        tmp_path,
        [
            {
                "tool_uses": [
                    {
                        "id": "child",
                        "name": "task",
                        "input": {
                            "preset": "executor",
                            "prompt": "write answer",
                        },
                    }
                ]
            },
            {
                "tool_uses": [
                    {
                        "id": "write",
                        "name": "write",
                        "input": {
                            "path": "answer",
                            "content": "42",
                        },
                    }
                ]
            },
        ],
    )
    runner.turn.policy = PermissionPolicy({"task": "allow", "write": "deny"})
    runner.turn.provider_factory = lambda _: provider
    result = await runner.run(state)
    final = await latest_loop(store, runner.callbacks.session_id)
    assert result.verification == "failed"
    assert final is not None and final.reason == "permission_denied"
    assert len(provider.requests) == 2
    assert not (tmp_path / "answer").exists()


async def test_verifier_scrubs_secret_environment(store: EventStore, tmp_path: Path) -> None:
    configure(tmp_path, python_command("import os; assert 'PERDIX_SECRET_TEST' not in os.environ"))
    runner, state, _ = await setup(store, tmp_path, [{"text": "done"}])
    runner.turn.sandbox = LocalBackend(base_env={"PERDIX_SECRET_TEST": "fixture-secret"})
    result = await runner.run(state)
    assert result.verification == "passed"


async def test_remembered_verification_permission_survives_reload(
    store: EventStore, tmp_path: Path
) -> None:
    command = python_command("pass")
    configure(tmp_path, command)
    runner, state, _ = await setup(store, tmp_path, [{"text": "done"}])

    async def always(_: PermissionRequest) -> PermissionOutcome:
        return PermissionOutcome(outcome="allow_always")

    callbacks = LoopCallbacks(runner.callbacks.session_id, asyncio.Event(), ignore, always)
    await GoalRunner(runner.turn, callbacks).run(state)
    record = await store.get_session(callbacks.session_id)
    assert record is not None
    agent = PerdixAgent(ServerConfig(credentials_path=tmp_path / "absent-credentials.json"))
    restored = agent._restore_state(record, await store.events(callbacks.session_id), str(tmp_path))
    assert restored.policy.evaluate("bash", {"command": command}) == "allow"
    assert restored.policy.evaluate("bash", {"command": "another command"}) == "ask"


async def test_reopens_interrupted_state_and_rejects_changed_config(tmp_path: Path) -> None:
    configure(tmp_path, python_command("pass"))
    store = await EventStore.open(tmp_path / "persist.db")
    try:
        runner, state, _ = await setup(store, tmp_path, [])
        sid = runner.callbacks.session_id
        await store.append(
            sid, "loop_state", state.model_copy(update={"iteration": 1, "steps_used": 3})
        )
    finally:
        await store.close()
    reopened = await EventStore.open(tmp_path / "persist.db")
    try:
        resumed = await LoopCommands(reopened, tmp_path.resolve()).prepare(sid, "resume", tmp_path)
        assert isinstance(resumed, LoopState) and resumed.steps_used == 3 and resumed.iteration == 1
        configure(tmp_path, python_command("raise SystemExit(2)"))
        rejected = await LoopCommands(reopened, tmp_path.resolve()).prepare(sid, "resume", tmp_path)
        assert isinstance(rejected, str)
    finally:
        await reopened.close()
