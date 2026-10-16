"""격리 후보를 통합하고 명시적 수락 의도 아래에서만 원본을 갱신한다."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from graphlib import TopologicalSorter
from pathlib import Path

from filelock import FileLock, Timeout

from perdix.workflows.models import AcceptanceReceipt, VerificationReceipt, WorkflowRun
from perdix.workflows.verification import VerificationService
from perdix.workflows.worktrees import CandidateCommit, WorktreeError, WorktreeService


class IntegrationError(RuntimeError):
    def __init__(self, code: str, detail: str, artifact_path: Path | None = None) -> None:
        self.code = code
        self.artifact_path = artifact_path
        super().__init__(detail)


class IntegrationService:
    """원본 잠금은 협조하는 Perdix 수락끼리만 직렬화한다."""

    def __init__(self, worktrees: WorktreeService, verification: VerificationService) -> None:
        self.worktrees = worktrees
        self.verification = verification

    async def integrate(
        self,
        run: WorkflowRun,
        candidates: Mapping[str, CandidateCommit],
        *,
        trusted: bool,
        cancel: asyncio.Event,
        attempt_id: str = "candidate-0",
        task_bases: Mapping[str, str] | None = None,
    ) -> CandidateCommit:
        """위상 순서의 작업별 변경만 합친다. 충돌 상태와 경로는 그대로 보존한다."""
        tasks = {task.task_id: task for task in run.plan.tasks}
        expected = {task.task_id for task in run.plan.tasks if task.role == "implement"}
        if set(candidates) != expected:
            raise IntegrationError("missing_candidates", "구현 작업별 후보가 필요합니다")
        if not candidates:
            raise IntegrationError("missing_candidates", "통합할 구현 후보가 없습니다")
        if task_bases is not None and set(task_bases) != expected:
            raise IntegrationError("missing_bases", "구현 작업별 기준 커밋이 필요합니다")
        first = next(iter(candidates.values())).worktree
        for task_id, candidate in candidates.items():
            ref = candidate.worktree
            if (
                ref.run_id != run.run_id
                or ref.task_id != task_id
                or ref.project_root != Path(run.project_root).resolve()
                or ref.base_commit != run.base_commit
                or ref.trusted_config_digest != first.trusted_config_digest
                or not await self.verification.matches(candidate, cancel=cancel)
            ):
                raise IntegrationError(
                    "candidate_mismatch", "작업 후보의 소유권 또는 내용이 다릅니다"
                )
        ref = await self.worktrees.prepare(
            project_root=Path(run.project_root),
            run_id=run.run_id,
            task_id="integration",
            attempt_id=attempt_id,
            trusted_config_json=first.trusted_config_json,
            trusted=trusted,
            cancel=cancel,
            base_commit=run.base_commit,
        )
        order = TopologicalSorter({key: task.dependencies for key, task in tasks.items()})
        try:
            for task_id in order.static_order():
                if task_id not in candidates:
                    continue
                candidate = candidates[task_id]
                base = task_bases[task_id] if task_bases is not None else candidate.base_commit
                await self.worktrees.git(
                    ref.path,
                    "merge-base",
                    "--is-ancestor",
                    base,
                    candidate.commit,
                    cancel=cancel,
                )
                commits = await self.worktrees.git(
                    ref.path,
                    "rev-list",
                    "--reverse",
                    "--topo-order",
                    f"{base}..{candidate.commit}",
                    cancel=cancel,
                )
                for commit in commits.splitlines():
                    await self.worktrees.git(
                        ref.path,
                        "-c",
                        "user.name=Perdix",
                        "-c",
                        "user.email=perdix@localhost",
                        "cherry-pick",
                        commit,
                        cancel=cancel,
                    )
        except WorktreeError as error:
            raise IntegrationError(
                "integration_blocked",
                "통합에 실패했습니다. 작업 경로를 보존합니다",
                ref.path,
            ) from error
        commit = await self.worktrees.git(ref.path, "rev-parse", "HEAD", cancel=cancel)
        tree = await self.worktrees.git(ref.path, "rev-parse", "HEAD^{tree}", cancel=cancel)
        return CandidateCommit(commit=commit, tree=tree, base_commit=run.base_commit, worktree=ref)

    async def accept(
        self,
        run: WorkflowRun,
        candidate: CandidateCommit,
        receipt: VerificationReceipt,
        *,
        intent: AcceptanceReceipt,
        cancel: asyncio.Event,
        reconcile_only: bool = False,
    ) -> AcceptanceReceipt:
        """호출자는 의도를 먼저 영속 append하고 반환 영수증을 이후 append한다."""
        ref = candidate.worktree
        if (
            ref.run_id != run.run_id
            or ref.task_id != "integration"
            or ref.project_root != Path(run.project_root).resolve()
            or ref.base_commit != run.base_commit
            or candidate.base_commit != run.base_commit
            or run.candidate_commit != candidate.commit
            or run.candidate_tree != candidate.tree
            or receipt.run_id != run.run_id
            or receipt.status != "passed"
            or receipt.owner_epoch > run.owner_epoch
            or receipt.candidate_commit != candidate.commit
            or receipt.candidate_tree != candidate.tree
            or receipt.config_digest != ref.trusted_config_digest
        ):
            raise IntegrationError("stale_receipt", "수락 후보와 검증 영수증이 일치하지 않습니다")
        if (
            intent.run_id != run.run_id
            or intent.verification_receipt_id != receipt.receipt_id
            or intent.base_commit != run.base_commit
            or intent.target_branch != run.target_branch
            or intent.candidate_commit != candidate.commit
            or intent.candidate_tree != candidate.tree
            or intent.owner_epoch > run.owner_epoch
            or intent.status != "awaiting"
        ):
            raise IntegrationError("invalid_intent", "영속 수락 의도가 후보와 일치하지 않습니다")
        if not await self.verification.matches(candidate, cancel=cancel):
            raise IntegrationError("stale_candidate", "후보 또는 신뢰 설정이 변경되었습니다")
        try:
            # 외부 에디터는 이 잠금을 따르지 않으므로 Git 직전·직후에도 검사한다.
            with FileLock(ref.common_git_dir / "perdix-accept.lock", timeout=0):
                if not await self.verification.matches(candidate, cancel=cancel):
                    raise IntegrationError(
                        "stale_candidate", "후보 또는 신뢰 설정이 변경되었습니다"
                    )
                root = ref.project_root
                common = Path(
                    await self.worktrees.git(
                        root,
                        "rev-parse",
                        "--path-format=absolute",
                        "--git-common-dir",
                        cancel=cancel,
                    )
                ).resolve()
                if common != ref.common_git_dir:
                    raise IntegrationError("project_drift", "원본 Git 저장소가 변경되었습니다")
                head = await self._target(run, cancel=cancel)
                if reconcile_only and head != candidate.commit:
                    raise IntegrationError("head_drift", "복구할 수락 커밋이 원본 HEAD와 다릅니다")
                if head not in (run.base_commit, candidate.commit):
                    raise IntegrationError("head_drift", "원본 HEAD가 기준 커밋에서 변경되었습니다")
                if head != candidate.commit:
                    await self.worktrees.git(
                        root,
                        "merge",
                        "--ff-only",
                        "--no-edit",
                        candidate.commit,
                        cancel=cancel,
                    )
                if await self._target(run, cancel=cancel) != candidate.commit:
                    raise IntegrationError("head_drift", "수락 이후 HEAD가 후보와 다릅니다")
                tree = await self.worktrees.git(root, "rev-parse", "HEAD^{tree}", cancel=cancel)
                if tree != candidate.tree:
                    raise IntegrationError("tree_drift", "수락 이후 tree가 검증 후보와 다릅니다")
                if not await self.verification.matches(candidate, cancel=cancel):
                    raise IntegrationError(
                        "stale_candidate", "수락 도중 후보 또는 설정이 변경되었습니다"
                    )
        except Timeout as error:
            raise IntegrationError(
                "target_busy", "다른 수락 작업이 원본을 잠그고 있습니다"
            ) from error
        except WorktreeError as error:
            raise IntegrationError("accept_blocked", "Git 수락에 실패했습니다") from error
        return intent.model_copy(
            update={
                "status": "accepted",
                "accepted_commit": candidate.commit,
                "owner_epoch": run.owner_epoch,
            }
        )

    async def _target(self, run: WorkflowRun, *, cancel: asyncio.Event) -> str:
        root = Path(run.project_root).resolve()
        branch = await self.worktrees.git(root, "symbolic-ref", "--short", "HEAD", cancel=cancel)
        if branch != run.target_branch:
            raise IntegrationError("branch_drift", "원본 브랜치가 변경되었습니다")
        if await self.worktrees.git(
            root,
            "status",
            "--porcelain",
            "--untracked-files=all",
            cancel=cancel,
        ):
            raise IntegrationError("dirty_target", "원본에 미커밋 또는 미추적 변경이 있습니다")
        return await self.worktrees.git(root, "rev-parse", "HEAD", cancel=cancel)
