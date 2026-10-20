"""워크플로 작업을 기존 TurnRunner와 격리 worktree에서 실행한다."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from perdix.context.messages import AssistantMessage, TextBlock
from perdix.harness.budget import Budget
from perdix.harness.events import PermissionOutcome, PermissionRequest, TurnStopReason
from perdix.harness.modes import Mode
from perdix.harness.turn import AskPermission, Sink, TurnResult, TurnRunner
from perdix.sandbox.base import ExecBackend
from perdix.sandbox.local import LocalBackend
from perdix.tools.registry import ToolRegistry
from perdix.workflows.execution_context import ExecutionContext
from perdix.workflows.models import TaskAttempt, WorkflowRun, WorkflowTask
from perdix.workflows.recovery import process_observer
from perdix.workflows.worktrees import WorktreeService


@dataclass(frozen=True, slots=True)
class HarnessOutcome:
    session_id: str
    text: str
    stop_reason: TurnStopReason


async def harness_prompt(
    parent: TurnRunner,
    *,
    cwd: Path,
    prompt: str,
    model: str,
    read_only: bool,
    cancel: asyncio.Event,
    sink: Sink,
    ask_permission: AskPermission,
    context: ExecutionContext | None = None,
    sandbox: ExecBackend | None = None,
) -> HarnessOutcome:
    """독립 대화 기록을 만들되 부모 취소·권한·공급자 계약을 상속한다."""
    factory = parent.provider_factory or parent.compactor_factory
    if model != parent.model and factory is None:
        raise RuntimeError("워크플로 모델 공급자 factory가 필요합니다")
    provider = factory(model) if factory is not None else parent.provider
    mode = Mode(
        id="plan" if read_only else "build",
        name="Workflow",
        description="워크플로 작업",
        read_only=read_only,
        verification=False,
    )
    # 별도 workflow 밖의 자식 실행과 프로젝트 밖 메모리 쓰기는 노출하지 않는다.
    registry = ToolRegistry(
        tool
        for name in parent._base_registry.names()
        if name not in {"task", "memory_append", "switch_mode", "team"}
        and (tool := parent._base_registry.get(name)) is not None
    )
    record = await parent.store.create_session(str(cwd), mode.id, provider.name, model)
    for name, selected in parent.auth_profiles.items():
        await parent.store.append(record.id, "config_change",
                                  {"key": "auth_profile_" + name, "value": selected})
    runner = TurnRunner(
        store=parent.store,
        provider=provider,
        registry=registry,
        policy=parent.policy,
        sandbox=sandbox or parent.sandbox,
        budget=Budget(shared_steps_limit=200),
        hooks=parent.hooks,
        mode=mode,
        cwd=cwd,
        system_prompt_builder=parent.system_prompt_builder,
        model=model,
        max_tokens=parent.max_tokens,
        effort=parent.effort,
        agents_config=parent.agents_config,
        compactor_factory=parent.compactor_factory,
        before_provider=context.reserve_step if context else None,
        before_tool=context.check_owner if context else None,
    )

    async def permission(request: PermissionRequest) -> PermissionOutcome:
        if context is None:
            return await ask_permission(request)
        return await context.permission(lambda: ask_permission(request))

    async def operation() -> TurnResult:
        return await runner.run(
            record.id,
            [TextBlock(text=prompt)],
            cancel=cancel,
            sink=sink,
            ask_permission=permission,
            origin="synthetic",
        )

    result = await context.run(operation) if context else await operation()
    events = await parent.store.events(record.id)
    text = next(
        (
            AssistantMessage.model_validate(e.payload).text()
            for e in reversed(events)
            if e.kind == "assistant_message"
        ),
        "",
    )
    return HarnessOutcome(record.id, text, result.stop_reason)


class TaskExecutor:
    """실행 context는 한 run 동안 공유하며 작업마다 새 worktree를 만든다."""

    def __init__(
        self,
        parent: TurnRunner,
        context: ExecutionContext,
        worktrees: WorktreeService,
        sink: Sink,
        ask_permission: AskPermission,
        trusted_config_json: str,
        repair_feedback: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self.parent = parent
        self.context = context
        self.worktrees = worktrees
        self.sink = sink
        self.ask_permission = ask_permission
        self.trusted_config_json = trusted_config_json
        self.repair_feedback = repair_feedback

    async def execute(
        self,
        run: WorkflowRun,
        task: WorkflowTask,
        attempt_id: str,
        predecessors: tuple[TaskAttempt, ...],
    ) -> TaskAttempt:
        async with self.context.task_slot():
            return await self.context.run(
                lambda: self._execute(run, task, attempt_id, predecessors)
            )

    async def _execute(
        self,
        run: WorkflowRun,
        task: WorkflowTask,
        attempt_id: str,
        predecessors: tuple[TaskAttempt, ...],
    ) -> TaskAttempt:
        await self.context.check_owner()
        if not isinstance(self.parent.sandbox, LocalBackend):
            raise RuntimeError("워크플로 실행에는 프로세스 관측 가능한 LocalBackend가 필요합니다")
        backend = self.parent.sandbox.with_process_observer(
            process_observer(
                self.context.owner.store,
                run.run_id,
                attempt_id,
                run.owner_epoch,
            )
        )
        worktrees = WorktreeService(backend, self.worktrees.storage_root)
        ref = await worktrees.prepare(
            project_root=Path(run.project_root),
            run_id=run.run_id,
            task_id=task.task_id,
            attempt_id=attempt_id,
            trusted_config_json=self.trusted_config_json,
            trusted=True,
            cancel=self.context.cancel,
            base_commit=run.base_commit,
        )

        await self.context.owner.store.save_attempt(
            TaskAttempt(
                run_id=run.run_id,
                task_id=task.task_id,
                attempt_id=attempt_id,
                owner_epoch=run.owner_epoch,
                status="running",
                worktree_path=str(ref.path),
                base_commit=ref.base_commit,
            )
        )

        async def apply(previous: TaskAttempt) -> None:
            if previous.candidate_commit and previous.base_commit:
                commits = await worktrees.git(
                    ref.path,
                    "rev-list",
                    "--reverse",
                    f"{previous.base_commit}..{previous.candidate_commit}",
                    cancel=self.context.cancel,
                )
                for commit in commits.splitlines():
                    await worktrees.git(
                        ref.path,
                        "-c",
                        "user.name=Perdix",
                        "-c",
                        "user.email=perdix@localhost",
                        "cherry-pick",
                        commit,
                        cancel=self.context.cancel,
                    )

        for previous in predecessors:
            if previous.task_id != task.task_id:
                await apply(previous)
        task_base = await worktrees.git(ref.path, "rev-parse", "HEAD", cancel=self.context.cancel)
        for previous in predecessors:
            if previous.task_id == task.task_id:
                await apply(previous)
        prompt = task.model_dump_json()
        if any(previous.task_id == task.task_id for previous in predecessors):
            snapshot = await self.context.owner.store.snapshot(run.run_id)
            if snapshot.verification is not None:
                prompt += "\n검증 결과:\n" + snapshot.verification.model_dump_json()
            if self.repair_feedback is not None:
                prompt += "\n리뷰 결과:\n" + await self.repair_feedback()
        outcome = await harness_prompt(
            self.parent,
            cwd=ref.path,
            prompt=prompt,
            model=task.model,
            read_only=task.role != "implement",
            cancel=self.context.cancel,
            sink=self.sink,
            ask_permission=self.ask_permission,
            context=self.context,
            sandbox=backend,
        )
        await self.context.check_owner()
        attempt = TaskAttempt(
            run_id=run.run_id,
            task_id=task.task_id,
            attempt_id=attempt_id,
            owner_epoch=run.owner_epoch,
            session_id=outcome.session_id,
            worktree_path=str(ref.path),
            base_commit=task_base,
            status="completed" if outcome.stop_reason == "end_turn" else "failed",
            failure_reason=None if outcome.stop_reason == "end_turn" else outcome.stop_reason,
        )
        if attempt.status == "completed":
            candidate = await worktrees.collect(ref, cancel=self.context.cancel)
            attempt = attempt.model_copy(
                update={"candidate_commit": candidate.commit, "candidate_tree": candidate.tree}
            )
        snapshot = await self.context.owner.store.snapshot(run.run_id)
        if any(
            p.attempt_id == attempt_id and (p.phase != "finished" or not p.quiescent)
            for p in snapshot.processes
        ):
            return attempt.model_copy(
                update={
                    "status": "unknown",
                    "failure_reason": "잔존 프로세스 종료를 확인할 수 없습니다",
                }
            )
        return attempt
