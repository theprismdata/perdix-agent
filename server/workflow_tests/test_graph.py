from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_worktrees import repository

from perdix.harness.budget import Budget
from perdix.harness.events import HarnessEvent, PermissionOutcome, PermissionRequest
from perdix.harness.hooks import HookBus
from perdix.harness.modes import GENERAL
from perdix.harness.turn import TurnRunner
from perdix.llm.fake import FakeProvider
from perdix.permissions.policy import PermissionPolicy
from perdix.session.store import EventStore
from perdix.tools.fs import WriteTool
from perdix.tools.registry import ToolRegistry
from perdix.tools.shell import BashTool
from perdix.workflows.execution_context import ExecutionContext
from perdix.workflows.executor import TaskExecutor
from perdix.workflows.graph import WorkflowGraph
from perdix.workflows.models import TaskAttempt, WorkflowPlan, WorkflowRun, WorkflowTask
from perdix.workflows.ownership import RunOwnership
from perdix.workflows.store import WorkflowStore


async def sink(event: HarnessEvent) -> None:
    return None


async def ask(request: PermissionRequest) -> PermissionOutcome:
    return PermissionOutcome(outcome="allow_once")


def task(key: str, dependencies: tuple[str, ...] = ()) -> WorkflowTask:
    return WorkflowTask(
        task_id=key,
        title=key,
        model=key,
        role="implement",
        paths=(f"{key}.txt",),
        acceptance=("file exists",),
        dependencies=dependencies,
    )


@pytest.mark.asyncio
async def test_real_harness_parallel_worktrees_and_join(tmp_path: Path) -> None:
    # Given: 실제 Git 저장소와 쓰기 도구를 호출하는 fake 공급자.
    root, worktrees, cancel = await repository(tmp_path)
    store = WorkflowStore(tmp_path / "workflow.sqlite")
    events = await EventStore.open(tmp_path / "events.sqlite")
    plan = WorkflowPlan(objective="parallel", tasks=(task("a"), task("b"), task("c", ("a", "b"))))
    head, branch = await worktrees.inspect(root, cancel=cancel)
    run = WorkflowRun(
        run_id="run",
        session_id="parent",
        project_root=str(root),
        base_commit=head,
        target_branch=branch,
        plan=plan,
    )
    await store.create_run(run)
    providers: dict[str, FakeProvider] = {}

    def factory(model: str) -> FakeProvider:
        provider = FakeProvider(
            (
                [
                    {
                        "tool_uses": [
                            {
                                "id": "commit-first",
                                "name": "bash",
                                "input": {
                                    "command": (
                                        "printf first > a-first.txt && git add a-first.txt && "
                                        "git -c user.name=Test -c user.email=test@localhost "
                                        "commit -m first"
                                    )
                                },
                            }
                        ]
                    }
                ]
                if model == "a"
                else []
            )
            + [
                {
                    "tool_uses": [
                        {
                            "id": f"write-{model}",
                            "name": "write",
                            "input": {"path": f"{model}.txt", "content": model},
                        }
                    ]
                },
                {"text": "done"},
            ],
            model=model,
        )
        providers[model] = provider
        return provider

    parent = TurnRunner(
        store=events,
        provider=factory("parent"),
        registry=ToolRegistry([WriteTool(), BashTool()]),
        policy=PermissionPolicy(),
        sandbox=worktrees.backend,
        budget=Budget(),
        hooks=HookBus(),
        mode=GENERAL,
        cwd=root,
        model="parent",
        system_prompt_builder=lambda cwd, mode: "",
        compactor_factory=factory,
    )
    try:
        async with RunOwnership(store, run.run_id, tmp_path / "locks") as owner:
            context = ExecutionContext(owner, cancel)
            current = (await store.snapshot(run.run_id)).run
            executor = TaskExecutor(parent, context, worktrees, sink, ask, "{}")
            graph = WorkflowGraph(store, executor, tmp_path / "checkpoints.sqlite")
            # When: 실제 StateGraph를 실행한다.
            snapshot = await graph.run(current)
        # Then: 합류에는 선행 파일이 있고 원본은 변경되지 않는다.
        assert snapshot.run.status == "completed"
        assert parent.mode.id == "general"
        attempts = {a.task_id: a for a in snapshot.attempts}
        assert len({a.worktree_path for a in attempts.values()}) == 3
        assert attempts["c"].worktree_path is not None
        joined = Path(attempts["c"].worktree_path)
        assert [(joined / f"{key}.txt").read_text() for key in "abc"] == list("abc")
        assert not (root / "a.txt").exists()
        assert (joined / "a-first.txt").read_text() == "first"
        assert snapshot.steps_used == 7
        assert (tmp_path / "checkpoints.sqlite").stat().st_size > 0
    finally:
        await events.close()


class RecordingExecutor:
    def __init__(self, *, fail: str | None = None) -> None:
        self.calls: list[str] = []
        self.fail = fail
        self.active = 0
        self.maximum = 0
        self.arrived = asyncio.Event()

    async def execute(
        self,
        run: WorkflowRun,
        task: WorkflowTask,
        attempt_id: str,
        predecessors: tuple[TaskAttempt, ...],
    ) -> TaskAttempt:
        self.calls.append(task.task_id)
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        if self.active == 2:
            self.arrived.set()
        if task.task_id in {"a", "b"}:
            await asyncio.wait_for(self.arrived.wait(), 3)
        self.active -= 1
        return TaskAttempt(
            run_id=run.run_id,
            task_id=task.task_id,
            attempt_id=attempt_id,
            owner_epoch=run.owner_epoch,
            status="failed" if task.task_id == self.fail else "completed",
        )


async def graph_case(tmp_path: Path, executor: RecordingExecutor, *, repair: bool = False):
    store = WorkflowStore(tmp_path / "workflow.sqlite")
    run = WorkflowRun(
        run_id="run",
        session_id="session",
        project_root=str(tmp_path),
        base_commit="base",
        target_branch="main",
        plan=WorkflowPlan(objective="test", tasks=(task("a"), task("b"), task("c", ("a", "b")))),
    )
    await store.create_run(run)

    async def finish(current: WorkflowRun, attempts: tuple[TaskAttempt, ...]) -> tuple[str, ...]:
        return ("a", "b") if repair else ()

    return await WorkflowGraph(store, executor, tmp_path / "checkpoint.sqlite", finish).run(run)


@pytest.mark.asyncio
async def test_failed_predecessor_never_dispatches_successor(tmp_path: Path) -> None:
    # Given
    executor = RecordingExecutor(fail="a")
    # When
    snapshot = await graph_case(tmp_path, executor)
    # Then
    assert snapshot.run.status == "blocked"
    assert sorted(executor.calls) == ["a", "b"]
    assert next(a for a in snapshot.attempts if a.task_id == "c").status == "blocked"
    assert executor.maximum == 2


@pytest.mark.asyncio
async def test_revision_limit_blocks_third_repair(tmp_path: Path) -> None:
    # Given
    executor = RecordingExecutor()
    # When
    snapshot = await graph_case(tmp_path, executor, repair=True)
    # Then
    assert snapshot.run.status == "blocked"
    assert executor.calls.count("a") == 3
    assert executor.calls.count("c") == 3


def test_cycle_rejected_before_execution() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        WorkflowPlan(objective="cycle", tasks=(task("a", ("b",)), task("b", ("a",))))


@pytest.mark.asyncio
async def test_completed_ledger_reused_without_provider_reexecution(tmp_path: Path) -> None:
    # Given
    executor = RecordingExecutor()
    first = await graph_case(tmp_path, executor)
    count = len(executor.calls)
    store = WorkflowStore(tmp_path / "workflow.sqlite")
    # When
    replay = await WorkflowGraph(store, executor, tmp_path / "checkpoint.sqlite").run(first.run)
    # Then
    assert replay.run.status == "completed"
    assert len(executor.calls) == count
    assert replay.attempts == first.attempts


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [False, True])
async def test_child_cannot_control_parent_team_or_mode(tmp_path: Path, read_only: bool) -> None:
    from perdix.sandbox.local import LocalBackend
    from perdix.teams.store import TeamStore
    from perdix.tools.mode import SwitchModeTool
    from perdix.tools.team import TeamTool
    from perdix.workflows.executor import harness_prompt

    # Given: 실제 팀 리더 도구와 부모 모드 변경 콜백.
    team = TeamStore(tmp_path / "teams.sqlite", tmp_path, "team", "leader", "session", "leader")
    await team.join()
    events = await EventStore.open(tmp_path / "events.sqlite")
    changed_modes: list[str] = []

    async def switch(mode: str) -> None:
        changed_modes.append(mode)

    provider = FakeProvider(
        [
            {
                "tool_uses": [
                    {"id": "stop", "name": "team", "input": {"action": "stop"}},
                    {"id": "mode", "name": "switch_mode", "input": {"mode": "build"}},
                ]
            },
            {"text": "done"},
        ]
    )
    parent = TurnRunner(
        store=events,
        provider=provider,
        registry=ToolRegistry([TeamTool(team), SwitchModeTool(switch), WriteTool()]),
        policy=PermissionPolicy(),
        sandbox=LocalBackend(),
        budget=Budget(),
        hooks=HookBus(),
        mode=GENERAL,
        cwd=tmp_path,
        model="fake-model",
        system_prompt_builder=lambda cwd, mode: "",
    )
    try:
        # When: 모델이 노출되지 않은 부모 제어 도구도 직접 호출한다.
        await harness_prompt(
            parent,
            cwd=tmp_path,
            prompt="task",
            model="fake-model",
            read_only=read_only,
            cancel=asyncio.Event(),
            sink=sink,
            ask_permission=ask,
        )
        # Then: wire 도구 목록과 실제 부모 상태 모두 격리된다.
        offered = {tool.name for tool in provider.requests[0].tools}
        assert not offered & {"team", "switch_mode"}
        assert ("write" not in offered) == read_only
        assert not await team.stopped()
        assert changed_modes == []
    finally:
        await events.close()


@pytest.mark.asyncio
async def test_unexpected_graph_fault_marks_run_failed_and_propagates(tmp_path: Path) -> None:
    class FailingExecutor(RecordingExecutor):
        async def execute(
            self,
            run: WorkflowRun,
            task: WorkflowTask,
            attempt_id: str,
            predecessors: tuple[TaskAttempt, ...],
        ) -> TaskAttempt:
            raise RuntimeError("unexpected executor fault")

    # Given
    executor = FailingExecutor()
    # When
    with pytest.raises(RuntimeError, match="unexpected executor fault"):
        await graph_case(tmp_path, executor)
    # Then
    snapshot = await WorkflowStore(tmp_path / "workflow.sqlite").snapshot("run")
    assert snapshot.run.status == "failed"
