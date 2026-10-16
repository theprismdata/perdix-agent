"""실제 Git 저장소에서 통합·명시적 수락과 crash 재호출을 검증한다."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from filelock import FileLock

from perdix.harness.events import PermissionOutcome, PermissionRequest
from perdix.permissions.policy import PermissionPolicy
from perdix.sandbox.local import LocalBackend
from perdix.workflows.integration import IntegrationError, IntegrationService
from perdix.workflows.models import (
    AcceptanceReceipt,
    VerificationReceipt,
    WorkflowPlan,
    WorkflowRun,
    WorkflowTask,
)
from perdix.workflows.verification import VerificationService
from perdix.workflows.worktrees import CandidateCommit, WorktreeService


async def allow(request: PermissionRequest) -> PermissionOutcome:
    return PermissionOutcome(outcome="allow_once")


@dataclass(frozen=True, slots=True)
class Scenario:
    service: IntegrationService
    run: WorkflowRun
    candidate: CandidateCommit
    receipt: VerificationReceipt
    intent: AcceptanceReceipt


async def setup(
    tmp_path: Path,
    *,
    conflict: bool = False,
    inherited: bool = False,
) -> Scenario:
    root = tmp_path / "original"
    root.mkdir()
    (root / ".perdix").mkdir()
    config = json.dumps(
        {
            "verify": {
                "commands": [
                    {
                        "name": "combined",
                        "run": "test -f first.txt && test -f second.txt",
                    }
                ]
            }
        }
    )
    (root / ".perdix/config.json").write_text(config)
    (root / "shared.txt").write_text("base\n")
    worktrees = WorktreeService(LocalBackend(), tmp_path / "trees")
    cancel = asyncio.Event()
    await worktrees.git(root, "init", "-b", "main", cancel=cancel)
    await worktrees.git(root, "add", ".", cancel=cancel)
    await worktrees.git(root, "commit", "-m", "baseline", cancel=cancel)
    base = await worktrees.git(root, "rev-parse", "HEAD", cancel=cancel)
    plan = WorkflowPlan(
        objective="combined",
        tasks=(
            WorkflowTask(
                task_id="second",
                title="second",
                role="implement",
                model="test",
                paths=("second.txt",),
                acceptance=("exists",),
                dependencies=("first",),
            ),
            WorkflowTask(
                task_id="first",
                title="first",
                role="implement",
                model="test",
                paths=("first.txt",),
                acceptance=("exists",),
            ),
        ),
    )
    run = WorkflowRun(
        run_id="run",
        session_id="session",
        project_root=str(root),
        base_commit=base,
        target_branch="main",
        plan=plan,
        owner_epoch=1,
    )
    verification = VerificationService(worktrees, PermissionPolicy({"bash": "allow"}), allow)
    service = IntegrationService(worktrees, verification)
    candidates: dict[str, CandidateCommit] = {}
    task_bases: dict[str, str] = {}
    for name in ("first", "second") if inherited else ("second", "first"):
        ref = await worktrees.prepare(
            project_root=root,
            run_id="run",
            task_id=name,
            attempt_id="attempt",
            trusted_config_json=config,
            trusted=True,
            cancel=cancel,
        )
        if inherited and name == "second":
            await worktrees.git(
                ref.path,
                "cherry-pick",
                candidates["first"].commit,
                cancel=cancel,
            )
        task_bases[name] = await worktrees.git(ref.path, "rev-parse", "HEAD", cancel=cancel)
        (ref.path / f"{name}.txt").write_text(name)
        if conflict:
            (ref.path / "shared.txt").write_text(name + "\n")
        candidates[name] = await worktrees.collect(ref, cancel=cancel)
    candidate = await service.integrate(
        run,
        candidates,
        trusted=True,
        cancel=cancel,
        task_bases=task_bases,
    )
    run = run.model_copy(
        update={"candidate_commit": candidate.commit, "candidate_tree": candidate.tree}
    )
    receipt = await verification.verify(candidate, cancel=cancel, owner_epoch=1)
    intent = AcceptanceReceipt(
        receipt_id="intent",
        run_id="run",
        verification_receipt_id=receipt.receipt_id,
        base_commit=base,
        candidate_commit=candidate.commit,
        candidate_tree=candidate.tree,
        target_branch="main",
        owner_epoch=1,
    )
    return Scenario(service, run, candidate, receipt, intent)


@pytest.mark.asyncio
async def test_combined_candidate_is_verified_and_original_waits(tmp_path: Path) -> None:
    # Given / When
    scene = await setup(tmp_path)
    # Then
    assert scene.receipt.status == "passed"
    assert scene.receipt.candidate_commit == scene.candidate.commit
    assert (scene.candidate.worktree.path / "first.txt").read_text() == "first"
    assert (scene.candidate.worktree.path / "second.txt").read_text() == "second"
    assert not (Path(scene.run.project_root) / "first.txt").exists()
    assert (
        await scene.service.worktrees.git(
            Path(scene.run.project_root),
            "rev-parse",
            "HEAD",
            cancel=asyncio.Event(),
        )
        == scene.run.base_commit
    )


@pytest.mark.asyncio
async def test_conflict_preserves_worktree_and_original(tmp_path: Path) -> None:
    # Given / When
    with pytest.raises(IntegrationError, match="통합") as caught:
        await setup(tmp_path, conflict=True)
    # Then
    assert caught.value.code == "integration_blocked"
    path = caught.value.artifact_path
    assert path is not None
    assert "<<<<<<<" in (path / "shared.txt").read_text()
    assert (tmp_path / "original/shared.txt").read_text() == "base\n"
    assert not (tmp_path / "original/first.txt").exists()


@pytest.mark.asyncio
async def test_explicit_accept_ff_and_duplicate_are_safe(tmp_path: Path) -> None:
    # Given
    scene = await setup(tmp_path)
    # When
    first = await scene.service.accept(
        scene.run,
        scene.candidate,
        scene.receipt,
        intent=scene.intent,
        cancel=asyncio.Event(),
    )
    second = await scene.service.accept(
        scene.run,
        scene.candidate,
        scene.receipt,
        intent=scene.intent,
        cancel=asyncio.Event(),
    )
    # Then
    assert first == second
    assert first.status == "accepted"
    assert (
        await scene.service.worktrees.git(
            Path(scene.run.project_root),
            "rev-parse",
            "HEAD",
            cancel=asyncio.Event(),
        )
        == scene.candidate.commit
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "tracked",
        "untracked",
        "head",
        "branch",
        "config",
        "candidate",
        "receipt",
        "intent",
    ],
)
async def test_accept_refuses_drift(tmp_path: Path, change: str) -> None:
    # Given
    scene = await setup(tmp_path)
    root = Path(scene.run.project_root)
    receipt, intent = scene.receipt, scene.intent
    match change:
        case "tracked":
            (root / "shared.txt").write_text("user edit")
        case "untracked":
            (root / "notes.txt").write_text("user note")
        case "head":
            await scene.service.worktrees.git(
                root,
                "commit",
                "--allow-empty",
                "-m",
                "user",
                cancel=asyncio.Event(),
            )
        case "branch":
            await scene.service.worktrees.git(
                root, "checkout", "-b", "other", cancel=asyncio.Event()
            )
        case "config":
            (root / ".perdix/config.json").write_text("{}")
        case "candidate":
            (scene.candidate.worktree.path / "first.txt").write_text("review changed code")
        case "receipt":
            receipt = receipt.model_copy(update={"candidate_tree": "different"})
        case "intent":
            intent = intent.model_copy(update={"verification_receipt_id": "unrelated"})
    before = await scene.service.worktrees.git(root, "rev-parse", "HEAD", cancel=asyncio.Event())
    # When
    with pytest.raises(IntegrationError):
        await scene.service.accept(
            scene.run,
            scene.candidate,
            receipt,
            intent=intent,
            cancel=asyncio.Event(),
        )
    # Then
    assert (
        await scene.service.worktrees.git(
            root,
            "rev-parse",
            "HEAD",
            cancel=asyncio.Event(),
        )
        == before
    )
    assert not (root / "first.txt").exists()


class SimulatedCrash(RuntimeError):
    pass


@pytest.mark.asyncio
async def test_post_ref_crash_replays_matching_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    scene = await setup(tmp_path)
    original = scene.service._target
    calls = 0

    async def crash_after_ref(run: WorkflowRun, *, cancel: asyncio.Event) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SimulatedCrash
        return await original(run, cancel=cancel)

    monkeypatch.setattr(scene.service, "_target", crash_after_ref)
    with pytest.raises(SimulatedCrash):
        await scene.service.accept(
            scene.run,
            scene.candidate,
            scene.receipt,
            intent=scene.intent,
            cancel=asyncio.Event(),
        )
    monkeypatch.setattr(scene.service, "_target", original)
    # When
    recovered = await scene.service.accept(
        scene.run,
        scene.candidate,
        scene.receipt,
        intent=scene.intent,
        cancel=asyncio.Event(),
        reconcile_only=True,
    )
    # Then
    assert recovered.status == "accepted"
    assert recovered.receipt_id == scene.intent.receipt_id
    assert recovered.accepted_commit == scene.candidate.commit


@pytest.mark.asyncio
async def test_reconciliation_refuses_head_reset_after_precheck(tmp_path: Path) -> None:
    # Given
    scene = await setup(tmp_path)
    cancel = asyncio.Event()
    root = Path(scene.run.project_root)
    await scene.service.accept(
        scene.run,
        scene.candidate,
        scene.receipt,
        intent=scene.intent,
        cancel=cancel,
    )
    assert (
        await scene.service.worktrees.git(
            root,
            "rev-parse",
            "HEAD",
            cancel=cancel,
        )
        == scene.candidate.commit
    )
    await scene.service.worktrees.git(
        root,
        "reset",
        "--hard",
        scene.run.base_commit,
        cancel=cancel,
    )
    # When
    with pytest.raises(IntegrationError) as caught:
        await scene.service.accept(
            scene.run,
            scene.candidate,
            scene.receipt,
            intent=scene.intent,
            cancel=cancel,
            reconcile_only=True,
        )
    # Then
    assert caught.value.code == "head_drift"
    assert (
        await scene.service.worktrees.git(
            root,
            "rev-parse",
            "HEAD",
            cancel=cancel,
        )
        == scene.run.base_commit
    )
    assert not (root / "first.txt").exists()


@pytest.mark.asyncio
async def test_cooperative_lock_blocks_other_accept(tmp_path: Path) -> None:
    # Given
    scene = await setup(tmp_path)
    # When
    with FileLock(scene.candidate.worktree.common_git_dir / "perdix-accept.lock", timeout=0):
        with pytest.raises(IntegrationError) as caught:
            await scene.service.accept(
                scene.run,
                scene.candidate,
                scene.receipt,
                intent=scene.intent,
                cancel=asyncio.Event(),
            )
    # Then
    assert caught.value.code == "target_busy"
    assert not (Path(scene.run.project_root) / "first.txt").exists()


@pytest.mark.asyncio
async def test_inherited_dependency_is_not_cherry_picked_twice(tmp_path: Path) -> None:
    # Given / When
    scene = await setup(tmp_path, inherited=True)
    # Then
    assert scene.receipt.status == "passed"
    assert (
        await scene.service.worktrees.git(
            scene.candidate.worktree.path,
            "rev-list",
            "--count",
            f"{scene.run.base_commit}..HEAD",
            cancel=asyncio.Event(),
        )
        == "2"
    )


@pytest.mark.asyncio
async def test_accept_does_not_launch_project_hook(tmp_path: Path) -> None:
    # Given
    scene = await setup(tmp_path)
    marker = tmp_path / "hook-ran"
    hook = scene.candidate.worktree.common_git_dir / "hooks/post-merge"
    hook.write_text(f"#!/bin/sh\nprintf unsafe > '{marker}'\n")
    hook.chmod(0o755)
    # When
    receipt = await scene.service.accept(
        scene.run,
        scene.candidate,
        scene.receipt,
        intent=scene.intent,
        cancel=asyncio.Event(),
    )
    # Then
    assert receipt.status == "accepted"
    assert not marker.exists()
