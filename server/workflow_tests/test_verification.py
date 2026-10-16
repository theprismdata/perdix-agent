"""실제 Git worktree와 셸 프로세스로 검증 영수증의 경계를 확인한다."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
import sys
from pathlib import Path

import pytest

from perdix.harness.events import PermissionOutcome, PermissionRequest
from perdix.permissions.policy import PermissionPolicy
from perdix.sandbox.local import LocalBackend
from perdix.workflows.verification import VerificationService
from perdix.workflows.worktrees import CandidateCommit, WorktreeService


async def allow(request: PermissionRequest) -> PermissionOutcome:
    return PermissionOutcome(outcome="allow_once")


async def candidate_for(tmp_path: Path, commands: list[str] | None) -> tuple[
    CandidateCommit, VerificationService,
]:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".perdix").mkdir()
    raw = json.dumps({"verify": {"commands": [
        {"name": f"check-{index}", "run": command, "timeout": 0.15}
        for index, command in enumerate(commands or [])
    ]}})
    if commands is None:
        raw = "{}"
    else:
        (root / ".perdix/config.json").write_text(raw)
    (root / "source.txt").write_text("original\n")
    worktrees = WorktreeService(LocalBackend(), tmp_path / "worktrees")
    cancel = asyncio.Event()
    await worktrees.git(root, "init", cancel=cancel)
    await worktrees.git(root, "add", ".", cancel=cancel)
    await worktrees.git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                        "commit", "-m", "Initial", cancel=cancel)
    ref = await worktrees.prepare(
        project_root=root, run_id="run", task_id="task", attempt_id="attempt",
        trusted_config_json=raw, trusted=True, cancel=cancel,
    )
    candidate = await worktrees.collect(ref, cancel=cancel)
    return candidate, VerificationService(worktrees, PermissionPolicy({"bash": "allow"}), allow)


@pytest.mark.asyncio
async def test_pass_records_real_outputs_and_immutable_digest(tmp_path: Path) -> None:
    # Given
    candidate, service = await candidate_for(tmp_path, ["printf success; printf diagnostic >&2"])
    # When
    receipt = await service.verify(candidate, cancel=asyncio.Event(), owner_epoch=7)
    # Then
    assert receipt.status == "passed"
    assert receipt.owner_epoch == 7
    assert receipt.commands[0].stdout == "success"
    assert receipt.commands[0].stderr == "diagnostic"
    artifact = receipt.artifacts[0]
    path = Path(artifact.path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact.sha256
    assert path.stat().st_mode & 0o222 == 0
    log = json.loads(path.read_text())
    assert log["cwd"] == str(candidate.worktree.path)
    assert log["started_at"] <= log["completed_at"]
    assert log["receipt"]["candidate_tree"] == candidate.tree


@pytest.mark.asyncio
@pytest.mark.parametrize(("commands", "status"), [
    ([], "unverified"), (["exit 9"], "failed"), (["sleep 30"], "failed"),
    (["printf changed > source.txt"], "stale"),
    (["printf created > new.txt"], "stale"),
    (["printf changed > .perdix/config.json"], "stale"),
])
async def test_non_success_never_passes(tmp_path: Path, commands: list[str], status: str) -> None:
    # Given
    candidate, service = await candidate_for(tmp_path, commands)
    # When
    receipt = await service.verify(candidate, cancel=asyncio.Event())
    # Then
    assert receipt.status == status
    assert Path(receipt.artifacts[0].path).is_file()
    if commands == ["sleep 30"]:
        assert receipt.commands[0].timed_out


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["config", "tracked", "untracked", "head"])
async def test_changed_candidate_is_rejected_before_execution(tmp_path: Path, change: str) -> None:
    # Given
    candidate, service = await candidate_for(tmp_path, ["printf should-not-run"])
    if change == "config":
        (candidate.worktree.project_root / ".perdix/config.json").write_text("{}")
    elif change == "tracked":
        (candidate.worktree.path / "source.txt").write_text("changed")
    elif change == "untracked":
        (candidate.worktree.path / "untracked").write_text("new")
    else:
        await service.worktrees.git(
            candidate.worktree.path, "-c", "user.name=Test", "-c", "user.email=t@example.com",
            "commit", "--allow-empty", "-m", "changed head", cancel=asyncio.Event(),
        )
    # When
    receipt = await service.verify(candidate, cancel=asyncio.Event())
    # Then
    assert receipt.status == "stale"
    assert receipt.commands == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["deny", "reject", "cancel", "config_during_ask"])
async def test_permission_gate_prevents_execution(tmp_path: Path, mode: str) -> None:
    # Given
    candidate, service = await candidate_for(tmp_path, ["printf should-not-run"])
    cancel = asyncio.Event()
    service.policy = PermissionPolicy({"bash": "deny" if mode == "deny" else "ask"})

    async def ask(request: PermissionRequest) -> PermissionOutcome:
        if mode == "cancel":
            cancel.set()
        if mode == "config_during_ask":
            (candidate.worktree.project_root / ".perdix/config.json").write_text("{}")
            return PermissionOutcome(outcome="allow_once")
        return PermissionOutcome(outcome="reject_once")

    service.ask_permission = ask
    # When
    receipt = await service.verify(candidate, cancel=cancel)
    # Then
    assert receipt.status == ("stale" if mode == "config_during_ask" else "failed")
    assert receipt.commands == ()


@pytest.mark.asyncio
async def test_secret_environment_is_scrubbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setenv("PERDIX_SECRET_TEST", "never-forward")
    candidate, service = await candidate_for(tmp_path, [
        'test -z "$PERDIX_SECRET_TEST" && printf sanitized',
    ])
    # When
    receipt = await service.verify(candidate, cancel=asyncio.Event())
    # Then
    assert receipt.status == "passed"
    assert receipt.commands[0].stdout == "sanitized"


@pytest.mark.asyncio
async def test_running_process_cancellation_is_recorded(tmp_path: Path) -> None:
    # Given
    cancel = asyncio.Event()

    async def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        cancel.set()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(connected, host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]
    script = (
        "import socket,time; "
        f"s=socket.create_connection(('127.0.0.1', {port})); time.sleep(30)"
    )
    candidate, service = await candidate_for(
        tmp_path, [f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"],
    )
    # When
    async with server:
        receipt = await service.verify(candidate, cancel=cancel)
    # Then
    assert receipt.status == "failed"
    assert receipt.commands[0].cancelled


@pytest.mark.asyncio
async def test_original_config_changed_during_command_is_stale(tmp_path: Path) -> None:
    # Given
    config = tmp_path / "repo/.perdix/config.json"
    candidate, service = await candidate_for(
        tmp_path, [f"printf '{{}}' > {shlex.quote(str(config))}"],
    )
    # When
    receipt = await service.verify(candidate, cancel=asyncio.Event())
    # Then
    assert receipt.status == "stale"
    assert receipt.commands[0].exit_code == 0


@pytest.mark.asyncio
async def test_absent_config_is_unverified(tmp_path: Path) -> None:
    # Given
    candidate, service = await candidate_for(tmp_path, None)
    # When
    receipt = await service.verify(candidate, cancel=asyncio.Event())
    # Then
    assert receipt.status == "unverified"
    assert receipt.commands == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["modify", "delete", "add"])
async def test_committed_candidate_config_drift_is_stale(tmp_path: Path, action: str) -> None:
    # Given
    candidate, service = await candidate_for(tmp_path, None if action == "add" else ["true"])
    config = candidate.worktree.path / ".perdix/config.json"
    if action == "delete":
        config.unlink()
    else:
        config.parent.mkdir(exist_ok=True)
        config.write_text('{"verify":{"commands":[{"name":"new","run":"false"}]}}')
    changed = await service.worktrees.collect(candidate.worktree, cancel=asyncio.Event())
    # When
    receipt = await service.verify(changed, cancel=asyncio.Event())
    # Then
    assert receipt.status == "stale"
    assert receipt.commands == ()
