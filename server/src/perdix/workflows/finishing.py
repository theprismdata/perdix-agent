"""통합 후보의 검증·읽기 전용 리뷰·명시적 수락."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from perdix.workflows.commands import WorkflowCommandError
from perdix.workflows.execution_context import ExecutionContext
from perdix.workflows.executor import harness_prompt
from perdix.workflows.integration import IntegrationService
from perdix.workflows.models import AcceptanceReceipt, TaskAttempt, VerificationReceipt, WorkflowRun
from perdix.workflows.projection import (
    AcceptanceRecorded,
    VerificationRecorded,
    WorkflowEvent,
    WorkflowSnapshot,
)
from perdix.workflows.store import WorkflowStore
from perdix.workflows.verification import VerificationService
from perdix.workflows.worktrees import CandidateCommit, WorktreeService

if TYPE_CHECKING:
    from perdix.workflows.service import WorkflowContext


class ReviewDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    repair_tasks: tuple[str, ...] = ()
    feedback: str = ""


class WorkflowFinisher:
    def __init__(self, store: WorkflowStore) -> None:
        self.store = store

    async def candidate(
        self,
        worktrees: WorktreeService,
        run: WorkflowRun,
        commit: str,
        tree: str,
        cancel: asyncio.Event,
        path: str | None = None,
    ) -> CandidateCommit:
        refs = [
            ref
            for ref in worktrees.preserved()
            if ref.run_id == run.run_id
            and (str(ref.path) == path if path else ref.task_id == "integration")
        ]
        for ref in refs:
            head = await worktrees.git(ref.path, "rev-parse", "HEAD", cancel=cancel)
            actual_tree = await worktrees.git(ref.path, "rev-parse", "HEAD^{tree}", cancel=cancel)
            if head == commit and actual_tree == tree:
                return CandidateCommit(
                    commit=commit, tree=tree, base_commit=run.base_commit, worktree=ref
                )
        raise WorkflowCommandError("검증 후보와 일치하는 등록 worktree가 없습니다")

    async def finish(
        self,
        run: WorkflowRun,
        attempts: tuple[TaskAttempt, ...],
        context: WorkflowContext,
        execution: ExecutionContext,
        worktrees: WorktreeService,
        integration: IntegrationService,
        verification: VerificationService,
    ) -> tuple[str, ...]:
        latest = {a.task_id: a for a in attempts if a.status == "completed"}
        candidates: dict[str, CandidateCommit] = {}
        bases: dict[str, str] = {}
        for task in run.plan.tasks:
            if task.role != "implement":
                continue
            attempt = latest[task.task_id]
            if (
                not attempt.candidate_commit
                or not attempt.candidate_tree
                or not attempt.base_commit
            ):
                raise WorkflowCommandError("구현 작업의 커밋 증거가 없습니다")
            candidates[task.task_id] = await self.candidate(
                worktrees,
                run,
                attempt.candidate_commit,
                attempt.candidate_tree,
                context.cancel,
                attempt.worktree_path,
            )
            bases[task.task_id] = attempt.base_commit
        candidate = await execution.run(
            lambda: integration.integrate(
                run,
                candidates,
                trusted=context.trusted,
                cancel=context.cancel,
                attempt_id="candidate-" + uuid4().hex,
                task_bases=bases,
            )
        )
        current = (await self.store.snapshot(run.run_id)).run.model_copy(
            update={
                "candidate_commit": candidate.commit,
                "candidate_tree": candidate.tree,
            }
        )
        await self.store.save_run(current)

        async def verify_and_record() -> VerificationReceipt:
            receipt = await verification.verify(
                candidate,
                cancel=context.cancel,
                owner_epoch=run.owner_epoch,
            )
            observed = await self.store.snapshot(run.run_id)
            if any(p.phase != "finished" or not p.quiescent for p in observed.processes):
                receipt = receipt.model_copy(update={"status": "failed"})
            await self.store.append_event(
                WorkflowEvent(
                    run_id=run.run_id,
                    owner_epoch=run.owner_epoch,
                    idempotency_key="verification-" + receipt.receipt_id,
                    payload=VerificationRecorded(receipt=receipt),
                )
            )
            return receipt

        receipt = await execution.run(verify_and_record)
        if receipt.status != "passed":
            return tuple(candidates)
        outcome = await harness_prompt(
            context.turn,
            cwd=candidate.worktree.path,
            model=context.turn.model,
            read_only=True,
            prompt=(
                "통합 후보를 읽기 전용으로 검토하세요. 수정이 필요하면 구현 task ID만 "
                "repair_tasks에 넣고, 승인하면 빈 배열인 JSON을 반환하세요. "
                + json.dumps(ReviewDecision.model_json_schema())
                + "\n"
                + run.plan.model_dump_json()
            ),
            cancel=context.cancel,
            sink=context.sink,
            ask_permission=context.ask_permission,
            context=execution,
            sandbox=worktrees.backend,
        )
        if outcome.stop_reason != "end_turn":
            raise WorkflowCommandError("후보 리뷰가 완료되지 않았습니다")
        decision = ReviewDecision.model_validate_json(outcome.text)
        if set(decision.repair_tasks) - candidates.keys():
            raise WorkflowCommandError("리뷰의 수정 대상 taskId가 잘못되었습니다")
        review_path = (
            worktrees.storage_root / run.run_id / "reviews" / (receipt.receipt_id + ".json")
        )
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text(decision.model_dump_json(), encoding="utf-8")
        return decision.repair_tasks

    async def accept(
        self,
        snapshot: WorkflowSnapshot,
        integration: IntegrationService,
        worktrees: WorktreeService,
        context: WorkflowContext,
    ) -> None:
        run, receipt = snapshot.run, snapshot.verification
        if (
            run.acceptance_status == "rejected"
            or run.status != "completed"
            or receipt is None
            or not run.candidate_commit
            or not run.candidate_tree
        ):
            raise WorkflowCommandError("검증과 리뷰가 완료된 후보가 필요합니다")
        candidate = await self.candidate(
            worktrees, run, run.candidate_commit, run.candidate_tree, context.cancel
        )
        intent = AcceptanceReceipt(
            receipt_id=uuid4().hex,
            run_id=run.run_id,
            verification_receipt_id=receipt.receipt_id,
            base_commit=run.base_commit,
            candidate_commit=candidate.commit,
            candidate_tree=candidate.tree,
            target_branch=run.target_branch,
            owner_epoch=run.owner_epoch,
        )
        head = await worktrees.git(context.project_root, "rev-parse", "HEAD", cancel=context.cancel)
        if head == candidate.commit:
            prior = snapshot.acceptance
            if prior is None or (
                prior.candidate_commit != candidate.commit
                or prior.candidate_tree != candidate.tree
                or prior.verification_receipt_id != receipt.receipt_id
                or prior.base_commit != run.base_commit
                or prior.target_branch != run.target_branch
            ):
                raise WorkflowCommandError("이전 수락 의도 없이 이미 변경된 HEAD입니다")
            intent = prior.model_copy(update={"owner_epoch": run.owner_epoch, "status": "awaiting"})
        await self.store.append_event(
            WorkflowEvent(
                run_id=run.run_id,
                owner_epoch=run.owner_epoch,
                idempotency_key=f"accept-intent-{intent.receipt_id}-{run.owner_epoch}",
                payload=AcceptanceRecorded(receipt=intent),
            )
        )
        accepted = await integration.accept(
            run, candidate, receipt, intent=intent, cancel=context.cancel
        )
        await self.store.append_event(
            WorkflowEvent(
                run_id=run.run_id,
                owner_epoch=run.owner_epoch,
                idempotency_key=f"accept-result-{intent.receipt_id}-{run.owner_epoch}",
                payload=AcceptanceRecorded(receipt=accepted),
            )
        )
