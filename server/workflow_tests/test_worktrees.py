from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from perdix.sandbox.base import ExecResult
from perdix.sandbox.local import LocalBackend
from perdix.workflows.worktrees import WorktreeError, WorktreeRef, WorktreeService


async def repository(tmp_path: Path) -> tuple[Path, WorktreeService, asyncio.Event]:
    root = tmp_path / "source"
    root.mkdir()
    service = WorktreeService(LocalBackend(), tmp_path / "tasks")
    cancel = asyncio.Event()
    await service.git(root, "init", "-b", "main", cancel=cancel)
    (root / "file.txt").write_text("original", encoding="utf-8")
    await service.git(root, "add", ".", cancel=cancel)
    await service.git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-m",
        "baseline",
        cancel=cancel,
    )
    return root, service, cancel


async def prepare(
    service: WorktreeService,
    root: Path,
    cancel: asyncio.Event,
    task: str = "task",
) -> WorktreeRef:
    return await service.prepare(
        project_root=root,
        run_id="run",
        task_id=task,
        attempt_id="attempt",
        trusted_config_json='{"verify": []}',
        trusted=True,
        cancel=cancel,
    )


@pytest.mark.asyncio
async def test_independent_worktrees_collect_without_changing_source(tmp_path: Path) -> None:
    root, service, cancel = await repository(tmp_path)
    base, branch = await service.inspect(root, cancel=cancel)
    one, two = await asyncio.gather(
        prepare(service, root, cancel, "one"), prepare(service, root, cancel, "two")
    )
    (one.path / "file.txt").write_text("one", encoding="utf-8")
    (two.path / "file.txt").write_text("two", encoding="utf-8")

    candidates = await asyncio.gather(
        service.collect(one, cancel=cancel), service.collect(two, cancel=cancel)
    )

    assert branch == "main"
    assert candidates[0].tree != candidates[1].tree
    assert all(candidate.commit != base for candidate in candidates)
    assert (root / "file.txt").read_text() == "original"
    assert await service.git(root, "rev-parse", "HEAD", cancel=cancel) == base
    assert await service.git(root, "status", "--porcelain", cancel=cancel) == ""
    assert (two.path / "file.txt").read_text() == "two"
    assert len(WorktreeService(LocalBackend(), service.storage_root).preserved()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("tracked", [False, True])
async def test_dirty_source_refused(tmp_path: Path, tracked: bool) -> None:
    root, service, cancel = await repository(tmp_path)
    (root / ("file.txt" if tracked else "untracked")).write_text("dirty")

    with pytest.raises(WorktreeError) as error:
        await prepare(service, root, cancel)

    assert error.value.code == "dirty_source"
    assert not service.storage_root.exists()


@pytest.mark.asyncio
async def test_non_git_refused(tmp_path: Path) -> None:
    service = WorktreeService(LocalBackend(), tmp_path / "tasks")
    root = tmp_path / "not-git"
    root.mkdir()

    with pytest.raises(WorktreeError) as error:
        await prepare(service, root, asyncio.Event())

    assert error.value.code == "git_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("task", ["../escape", "UPPER", "--option", "a/b"])
async def test_invalid_ids_refused(tmp_path: Path, task: str) -> None:
    root, service, cancel = await repository(tmp_path)

    with pytest.raises(WorktreeError) as error:
        await prepare(service, root, cancel, task)

    assert error.value.code == "invalid_id"


@pytest.mark.asyncio
async def test_collect_rejects_external_symlink(tmp_path: Path) -> None:
    root, service, cancel = await repository(tmp_path)
    ref = await prepare(service, root, cancel)
    (ref.path / "escape").symlink_to(root / "file.txt")

    with pytest.raises(WorktreeError) as error:
        await service.collect(ref, cancel=cancel)

    assert error.value.code == "path_escape"
    assert (root / "file.txt").read_text() == "original"


@pytest.mark.asyncio
async def test_frozen_config_cannot_be_replaced(tmp_path: Path) -> None:
    root, service, cancel = await repository(tmp_path)
    ref = await prepare(service, root, cancel)
    altered = ref.model_copy(update={"trusted_config_json": '{"mcp": "unsafe"}'})

    with pytest.raises(WorktreeError) as error:
        await service.validate(altered, cancel=cancel)

    assert error.value.code == "registry_mismatch"


@pytest.mark.asyncio
async def test_cancel_does_not_create_worktree(tmp_path: Path) -> None:
    root, service, cancel = await repository(tmp_path)
    cancel.set()

    with pytest.raises(asyncio.CancelledError):
        await prepare(service, root, cancel)

    assert not service.storage_root.exists()


@pytest.mark.asyncio
async def test_hooks_disabled_and_env_extra_scrubbed(tmp_path: Path) -> None:
    root, service, cancel = await repository(tmp_path)
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 72\n")
    hook.chmod(0o755)
    ref = await prepare(service, root, cancel)
    (ref.path / "file.txt").write_text("changed")

    candidate = await service.collect(ref, cancel=cancel)

    assert candidate.commit != ref.base_commit
    backend = LocalBackend(base_env={"PATH": "/usr/bin:/bin", "API_KEY": "inherited"})
    service_with_env = WorktreeService(
        backend,
        tmp_path / "other",
        env_extra={
            "API_KEY": "override",
            "DATABASE_URL": "postgres://a:b@host",
            "VISIBLE": "yes",
        },
    )
    result = await backend.run_argv(
        [
            sys.executable,
            "-c",
            "import os; assert 'API_KEY' not in os.environ; "
            "assert 'DATABASE_URL' not in os.environ; assert os.environ['VISIBLE'] == 'yes'",
        ],
        cwd=root,
        timeout=10,
        cancel=cancel,
        env_extra=service_with_env._env,
    )
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_local_filter_refused_before_execution(tmp_path: Path) -> None:
    root, service, cancel = await repository(tmp_path)
    config = root / ".git" / "config"
    with config.open("a") as stream:
        stream.write('\n[filter "danger"]\n\tclean = touch SHOULD_NOT_EXIST\n')

    with pytest.raises(WorktreeError) as error:
        await prepare(service, root, cancel)

    assert error.value.code == "unsafe_git_config"
    assert not (root / "SHOULD_NOT_EXIST").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trusted,base,code",
    [
        (False, None, "untrusted"),
        (True, "0" * 40, "base_changed"),
    ],
)
async def test_untrusted_or_changed_base_refused(
    tmp_path: Path,
    trusted: bool,
    base: str | None,
    code: str,
) -> None:
    root, service, cancel = await repository(tmp_path)

    with pytest.raises(WorktreeError) as error:
        await service.prepare(
            project_root=root,
            run_id="run",
            task_id="task",
            attempt_id="attempt",
            trusted_config_json="{}",
            trusted=trusted,
            cancel=cancel,
            base_commit=base,
        )

    assert error.value.code == code
    assert not service.storage_root.exists()


@pytest.mark.asyncio
async def test_storage_symlink_escape_refused(tmp_path: Path) -> None:
    root, service, cancel = await repository(tmp_path)
    service.storage_root.mkdir()
    (service.storage_root / "run").symlink_to(root, target_is_directory=True)

    with pytest.raises(WorktreeError) as error:
        await prepare(service, root, cancel)

    assert error.value.code == "path_escape"
    assert not (root / "task").exists()


class SlowBackend(LocalBackend):
    async def run_argv(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
        cancel: asyncio.Event,
        env_extra: dict[str, str] | None = None,
    ) -> ExecResult:
        return await super().run_argv(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=cwd,
            timeout=timeout,
            cancel=cancel,
            env_extra=env_extra,
        )


@pytest.mark.asyncio
async def test_parent_cancellation_stops_inflight_subprocess(tmp_path: Path) -> None:
    cancel = asyncio.Event()
    service = WorktreeService(SlowBackend(), tmp_path / "tasks")
    timer = asyncio.get_running_loop().call_later(0.1, cancel.set)

    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(service.git(tmp_path, "status", cancel=cancel), timeout=10)
    finally:
        timer.cancel()

    assert cancel.is_set()
