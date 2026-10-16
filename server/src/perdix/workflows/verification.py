"""고정 후보와 신뢰된 설정에 결합된 실행 증거를 생성한다."""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from datetime import UTC, datetime
from typing import assert_never

from pydantic import BaseModel, ConfigDict

from perdix.config.project import ProjectConfig, config_hash
from perdix.harness.events import PermissionRequest
from perdix.harness.turn import AskPermission, TurnRunner
from perdix.permissions.policy import PermissionPolicy
from perdix.workflows.models import (
    ArtifactRef,
    VerificationCommandResult,
    VerificationReceipt,
    VerificationStatus,
)
from perdix.workflows.worktrees import CandidateCommit, WorktreeError, WorktreeService


class VerificationLog(BaseModel):
    model_config = ConfigDict(frozen=True)
    cwd: str
    started_at: str
    completed_at: str
    reason: str
    receipt: VerificationReceipt


class VerificationService:
    """명령은 순차 실행하고 권한 질문은 서비스 내에서 직렬화한다."""

    def __init__(
        self, worktrees: WorktreeService, policy: PermissionPolicy, ask_permission: AskPermission,
    ) -> None:
        self.worktrees = worktrees
        self.policy = policy
        self.ask_permission = ask_permission
        self._permission_lock = asyncio.Lock()

    async def matches(self, candidate: CandidateCommit, *, cancel: asyncio.Event) -> bool:
        """후보의 등록 정보, 설정, HEAD/tree와 tracked/untracked 청결을 확인한다."""
        ref = candidate.worktree
        try:
            await self.worktrees.validate(ref, cancel=cancel)
            try:
                digest = config_hash(ref.project_root)
            except FileNotFoundError:
                digest = hashlib.sha256(b"{}").hexdigest()
            if digest != ref.trusted_config_digest:
                return False
            tracked_config = await self.worktrees.git(
                ref.path, "ls-tree", ref.base_commit, "--", ".perdix/config.json", cancel=cancel,
            )
            try:
                candidate_digest = config_hash(ref.path)
            except FileNotFoundError:
                if tracked_config:
                    return False
            else:
                if candidate_digest != ref.trusted_config_digest:
                    return False
            if candidate.base_commit != ref.base_commit:
                return False
            head = await self.worktrees.git(ref.path, "rev-parse", "HEAD", cancel=cancel)
            tree = await self.worktrees.git(ref.path, "rev-parse", "HEAD^{tree}", cancel=cancel)
            dirty = await self.worktrees.git(
                ref.path, "status", "--porcelain", "--untracked-files=all", cancel=cancel,
            )
            return head == candidate.commit and tree == candidate.tree and not dirty
        except (OSError, WorktreeError):
            return False

    async def _allowed(self, command: str, cancel: asyncio.Event) -> bool:
        async with self._permission_lock:
            if cancel.is_set():
                return False
            payload = {"command": command}
            decision = self.policy.evaluate("bash", payload)
            match decision:
                case "deny":
                    return False
                case "allow":
                    return True
                case "ask":
                    request = PermissionRequest(
                        tool_use_id=f"verify_{uuid.uuid4().hex}", name="bash", kind="execute",
                        title="워크플로 검증", input=payload,
                    )
                    outcome = await TurnRunner._ask_until_cancelled(
                        self.ask_permission, request, cancel,
                    )
                    match outcome.outcome:
                        case "allow_once":
                            return True
                        case "allow_always":
                            self.policy.remember("bash", payload, "allow")
                            return True
                        case "reject_always":
                            self.policy.remember("bash", payload, "deny")
                            return False
                        case "reject_once" | "cancelled":
                            return False
                        case unreachable:
                            assert_never(unreachable)
                case unreachable_decision:
                    assert_never(unreachable_decision)

    async def verify(
        self, candidate: CandidateCommit, *, cancel: asyncio.Event, owner_epoch: int = 0,
    ) -> VerificationReceipt:
        """영수증만 반환하며 저장소 append와 소유권 트랜잭션은 호출자가 담당한다."""
        started = datetime.now(UTC).isoformat()
        receipt = VerificationReceipt(
            receipt_id=uuid.uuid4().hex, run_id=candidate.worktree.run_id,
            candidate_commit=candidate.commit, candidate_tree=candidate.tree,
            config_digest=candidate.worktree.trusted_config_digest, owner_epoch=owner_epoch,
        )
        results: list[VerificationCommandResult] = []
        status: VerificationStatus = "unverified"
        reason = "no_commands"
        active_command: str | None = None
        try:
            if not await self.matches(candidate, cancel=cancel):
                status, reason = "stale", "candidate_or_config_changed"
            else:
                config = ProjectConfig.model_validate_json(candidate.worktree.trusted_config_json)
                for command in config.verify.commands:
                    if not await self._allowed(command.run, cancel):
                        status, reason = "failed", "permission_denied_or_cancelled"
                        break
                    if not await self.matches(candidate, cancel=cancel):
                        status, reason = "stale", "candidate_or_config_changed"
                        break
                    active_command = command.run
                    clock = time.monotonic()
                    output = await self.worktrees.backend.run(
                        command.run, cwd=candidate.worktree.path, timeout=command.timeout,
                        cancel=cancel,
                    )
                    results.append(VerificationCommandResult(
                        command=command.run, exit_code=output.exit_code,
                        timed_out=output.timed_out, cancelled=output.cancelled,
                        stdout=output.stdout, stderr=output.stderr,
                        duration_seconds=time.monotonic() - clock,
                    ))
                    active_command = None
                    if output.cancelled or cancel.is_set():
                        status, reason = "failed", "cancelled"
                        break
                    if not await self.matches(candidate, cancel=cancel):
                        status, reason = "stale", "candidate_or_config_changed"
                        break
                    if output.exit_code != 0 or output.timed_out:
                        status, reason = "failed", "command_failed"
                        break
                    status, reason = "passed", "commands_passed"
        except asyncio.CancelledError:
            status, reason = "failed", "cancelled"
            if active_command is not None:
                results.append(VerificationCommandResult(command=active_command, cancelled=True))
        except OSError as error:
            status, reason = "failed", "execution_error"
            if active_command is not None:
                results.append(VerificationCommandResult(command=active_command, stderr=str(error)))
        receipt = receipt.model_copy(update={"status": status, "commands": tuple(results)})
        log = VerificationLog(
            cwd=str(candidate.worktree.path), started_at=started,
            completed_at=datetime.now(UTC).isoformat(), reason=reason, receipt=receipt,
        ).model_dump_json()
        location = candidate.worktree.path.parent / "verification"
        location.mkdir(exist_ok=True)
        path = location / f"{receipt.receipt_id}.json"
        with path.open("x", encoding="utf-8") as stream:
            stream.write(log)
        path.chmod(0o444)
        artifact = ArtifactRef(
            artifact_id=receipt.receipt_id, path=str(path), kind="verification_log",
            sha256=hashlib.sha256(log.encode()).hexdigest(),
        )
        return receipt.model_copy(update={"artifacts": (artifact,)})
