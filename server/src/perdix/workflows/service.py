"""ACP 명령을 계획·격리 실행·검증·명시적 수락에 연결한다."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never
from uuid import uuid4

from pydantic import ValidationError

from perdix.harness.budget import BudgetExceeded
from perdix.harness.events import TextDelta
from perdix.harness.turn import AskPermission, Sink, TurnRunner
from perdix.llm.base import ProviderError
from perdix.workflows.commands import WorkflowCommand, WorkflowCommandError
from perdix.workflows.execution_context import ExecutionContext
from perdix.workflows.executor import TaskExecutor, harness_prompt
from perdix.workflows.finishing import WorkflowFinisher
from perdix.workflows.graph import WorkflowGraph
from perdix.workflows.integration import IntegrationError, IntegrationService
from perdix.workflows.models import (
    TaskAttempt,
    WorkflowPlan,
    WorkflowRun,
)
from perdix.workflows.ownership import RunOwnership
from perdix.workflows.phases import WorkflowPhases
from perdix.workflows.projection import (
    WorkflowSnapshot,
)
from perdix.workflows.recovery import reconcile_acceptance, reconcile_run
from perdix.workflows.store import WorkflowStore
from perdix.workflows.verification import VerificationService
from perdix.workflows.worktrees import WorktreeError, WorktreeService


class WorkflowAccessError(ValueError):
    """다른 프로젝트나 세션의 실행에는 접근할 수 없다."""


@dataclass(frozen=True, slots=True)
class WorkflowContext:
    session_id: str
    project_root: Path
    turn: TurnRunner
    cancel: asyncio.Event
    sink: Sink
    ask_permission: AskPermission
    trusted: bool
    trusted_config_json: str


class WorkflowService:
    """서비스 인스턴스 재생성 후에도 ledger와 등록 worktree를 재사용한다."""

    def __init__(self, store: WorkflowStore, storage_root: Path) -> None:
        self.store = store
        self.storage_root = storage_root.resolve()
        self.finisher = WorkflowFinisher(store)
        self.phases = WorkflowPhases(store, self.storage_root)

    def _worktrees(self, context: WorkflowContext) -> WorktreeService:
        return WorktreeService(context.turn.sandbox, self.storage_root / "worktrees")

    async def status(self, run_id: str, project_root: Path) -> WorkflowSnapshot:
        snapshot = await self.store.snapshot(run_id)
        if Path(snapshot.run.project_root).resolve() != project_root.resolve():
            raise WorkflowAccessError("다른 프로젝트의 workflow입니다")
        return snapshot

    async def plan(self, request: str, context: WorkflowContext) -> WorkflowSnapshot:
        worktrees = self._worktrees(context)
        root = context.project_root.resolve()
        try:
            actual = await worktrees.git(
                root, "rev-parse", "--show-toplevel", cancel=context.cancel,
            )
        except WorktreeError as exc:
            raise WorkflowCommandError(
                "워크플로 계획을 위한 Git 저장소 확인에 실패했습니다. "
                "Git 저장소 루트에서 Perdix를 시작하세요. "
                "분석·질문은 /workflow plan 없이 일반 프롬프트로 요청할 수 있습니다. "
                f"원인: {exc}"
            ) from exc
        if Path(actual).resolve() != root:
            raise WorkflowAccessError("저장소 루트에서 계획을 생성해야 합니다")
        base = await worktrees.git(root, "rev-parse", "HEAD", cancel=context.cancel)
        branch = await worktrees.git(root, "symbolic-ref", "--short", "HEAD", cancel=context.cancel)
        if request.lstrip().startswith("{"):
            plan = WorkflowPlan.model_validate_json(request)
        else:
            outcome = await harness_prompt(
                context.turn,
                cwd=root,
                model=context.turn.model,
                read_only=True,
                prompt=(
                    "요청을 분석하여 실행 전 검토할 WorkflowPlan JSON 하나만 반환하세요. "
                    "분석 작업 뒤 독립 구현 작업을 의존성 DAG로 나누세요. "
                    "경로는 저장소 상대 경로, model은 " + context.turn.model + ". "
                    "스키마: " + json.dumps(WorkflowPlan.model_json_schema()) + "\n요청: " + request
                ),
                cancel=context.cancel,
                sink=context.sink,
                ask_permission=context.ask_permission,
            )
            if outcome.stop_reason != "end_turn":
                raise WorkflowCommandError("계획 생성이 완료되지 않았습니다")
            plan = WorkflowPlan.model_validate_json(outcome.text)
        run = WorkflowRun(
            run_id=uuid4().hex,
            session_id=context.session_id,
            project_root=str(root),
            base_commit=base,
            target_branch=branch,
            plan=plan,
        )
        if not any(task.role == "implement" for task in plan.tasks):
            raise WorkflowCommandError("계획에 구현 작업이 필요합니다")
        frozen = self.storage_root / "configs" / (run.run_id + ".json")
        frozen.parent.mkdir(parents=True, exist_ok=True)
        frozen.write_text(context.trusted_config_json, encoding="utf-8")
        return await self.store.create_run(run)

    async def execute(
        self,
        command: WorkflowCommand,
        context: WorkflowContext,
    ) -> WorkflowSnapshot:
        if command.action == "plan":
            return await self.plan(command.request, context)
        if command.run_id is None:
            raise WorkflowCommandError("runId가 필요합니다")
        snapshot = await self.status(command.run_id, context.project_root)
        if snapshot.run.session_id != context.session_id:
            raise WorkflowAccessError("이 workflow를 만든 세션이 아닙니다")
        if command.action == "status":
            return snapshot
        if snapshot.run.graph_version != 1:
            raise WorkflowCommandError("지원하지 않는 workflow 버전은 조회만 가능합니다")
        if not context.trusted:
            raise WorkflowAccessError("workflow 실행에는 프로젝트 신뢰가 필요합니다")
        frozen = self.storage_root / "configs" / (command.run_id + ".json")
        if frozen.read_text(encoding="utf-8") != context.trusted_config_json:
            raise WorkflowCommandError("계획 당시 프로젝트 설정과 다릅니다")
        async with RunOwnership(self.store, command.run_id, self.storage_root / "locks") as owner:
            snapshot = await self.store.snapshot(command.run_id)
            run = snapshot.run
            execution = ExecutionContext(owner, context.cancel)
            worktrees = self._worktrees(context)
            verification = VerificationService(
                worktrees, context.turn.policy, context.ask_permission
            )
            integration = IntegrationService(worktrees, verification)
            if (
                command.action in {"resume", "accept"}
                and snapshot.acceptance is not None
                and run.candidate_commit
                and run.candidate_tree
            ):
                candidate = await self.finisher.candidate(
                    worktrees,
                    run,
                    run.candidate_commit,
                    run.candidate_tree,
                    context.cancel,
                )
                if await reconcile_acceptance(
                    self.store,
                    run.run_id,
                    integration,
                    candidate,
                    cancel=context.cancel,
                ):
                    return await self.store.snapshot(run.run_id)
            match command.action:
                case "accept":
                    recovery = await reconcile_run(self.store, run.run_id)
                    if recovery.read_only or not recovery.resumable:
                        raise WorkflowCommandError("이전 실행의 종료가 확인되지 않았습니다")
                    async with self.phases.observe(run, "accept", context) as phase_worktrees:
                        phase_verification = VerificationService(
                            phase_worktrees,
                            context.turn.policy,
                            context.ask_permission,
                        )
                        await self.finisher.accept(
                            snapshot,
                            IntegrationService(
                                phase_worktrees,
                                phase_verification,
                            ),
                            phase_worktrees,
                            context,
                        )
                case "reject":
                    if run.acceptance_status == "accepted":
                        raise WorkflowCommandError("이미 반영한 workflow는 거절할 수 없습니다")
                    await self.store.save_run(
                        run.model_copy(update={"acceptance_status": "rejected"})
                    )
                case "start" | "resume" | "retry":
                    if run.acceptance_status != "awaiting":
                        raise WorkflowCommandError("종료된 workflow는 실행할 수 없습니다")
                    if command.action == "start" and (run.status != "pending" or snapshot.attempts):
                        raise WorkflowCommandError("이미 시작된 workflow는 resume을 사용하세요")
                    retry: tuple[str, ...] = ()
                    if command.action in {"resume", "retry"}:
                        recovered = await reconcile_run(self.store, run.run_id)
                        if recovered.read_only:
                            raise WorkflowCommandError("지원하지 않는 workflow 버전입니다")
                        if command.action == "resume" and not recovered.resumable:
                            return await self.store.snapshot(run.run_id)
                        if (
                            command.action == "retry"
                            and command.task_id not in recovered.retryable_task_ids
                        ):
                            raise WorkflowCommandError(
                                "종료가 확인된 실패 작업만 재시도할 수 있습니다"
                            )
                        run = (await self.store.snapshot(run.run_id)).run
                    if command.action == "retry":
                        if command.task_id not in {t.task_id for t in run.plan.tasks}:
                            raise WorkflowCommandError("존재하지 않는 taskId입니다")
                        assert command.task_id is not None
                        retry = (command.task_id,)

                    async def repair_feedback() -> str:
                        current = await self.store.snapshot(run.run_id)
                        if current.verification is None:
                            return ""
                        path = (
                            worktrees.storage_root
                            / run.run_id
                            / "reviews"
                            / (current.verification.receipt_id + ".json")
                        )
                        return path.read_text(encoding="utf-8") if path.is_file() else ""

                    executor = TaskExecutor(
                        context.turn,
                        execution,
                        worktrees,
                        context.sink,
                        context.ask_permission,
                        context.trusted_config_json,
                        repair_feedback=repair_feedback,
                    )

                    async def finish(
                        current: WorkflowRun, attempts: tuple[TaskAttempt, ...]
                    ) -> tuple[str, ...]:
                        async with self.phases.observe(
                            current, "finish", context
                        ) as phase_worktrees:
                            phase_verification = VerificationService(
                                phase_worktrees,
                                context.turn.policy,
                                context.ask_permission,
                            )
                            return await self.finisher.finish(
                                current,
                                attempts,
                                context,
                                execution,
                                phase_worktrees,
                                IntegrationService(phase_worktrees, phase_verification),
                                phase_verification,
                            )

                    graph = WorkflowGraph(
                        self.store,
                        executor,
                        self.storage_root / "checkpoints.sqlite3",
                        finish=finish,
                    )
                    await self._run_graph(graph, run, retry, context)
                case "plan" | "status":
                    raise WorkflowCommandError("명령 경로가 잘못되었습니다")
                case unreachable:
                    assert_never(unreachable)
        return await self.store.snapshot(command.run_id)

    async def _run_graph(
        self,
        graph: WorkflowGraph,
        run: WorkflowRun,
        retry: tuple[str, ...],
        context: WorkflowContext,
    ) -> None:
        try:
            await graph.run(run, retry_tasks=retry)
        except (
            IntegrationError,
            WorktreeError,
            WorkflowCommandError,
            ValidationError,
            BudgetExceeded,
            ProviderError,
        ) as error:
            current = (await self.store.snapshot(run.run_id)).run
            await self.store.save_run(
                current.model_copy(
                    update={
                        "status": "blocked",
                        "failure_reason": str(error),
                    }
                )
            )
            failure = self.storage_root / "failures" / run.run_id / (uuid4().hex + ".txt")
            failure.parent.mkdir(parents=True, exist_ok=True)
            failure.write_text(str(error), encoding="utf-8")
            await context.sink(TextDelta(text=f"워크플로 중단: {error}\n증거: {failure}"))
