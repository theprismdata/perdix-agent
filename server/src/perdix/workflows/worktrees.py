"""원본을 건드리지 않는 작업별 Git worktree와 고정 신뢰 스냅샷."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from perdix.sandbox.base import ExecBackend, scrub_env


class WorktreeError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


class WorktreeRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_root: Path
    common_git_dir: Path
    base_commit: str
    path: Path
    branch: str
    run_id: str
    task_id: str
    attempt_id: str
    trusted_config_json: str
    trusted_config_digest: str


class CandidateCommit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    commit: str
    tree: str
    base_commit: str
    worktree: WorktreeRef


class WorktreeService:
    """등록 파일을 영속 보관한다. 실패한 worktree도 자동 삭제하지 않는다."""

    def __init__(
        self,
        backend: ExecBackend,
        storage_root: Path,
        *,
        env_extra: Mapping[str, str] | None = None,
    ) -> None:
        self.backend = backend
        self.storage_root = storage_root.resolve()
        self._env = scrub_env(env_extra or {})

    def _location(self, run_id: str, task_id: str, attempt_id: str) -> Path:
        for value in (run_id, task_id, attempt_id):
            if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", value) is None:
                raise WorktreeError("invalid_id", "작업 ID는 정규화된 소문자 식별자여야 합니다")
        path = self.storage_root / run_id / task_id / attempt_id
        if path.resolve() != path:
            raise WorktreeError("path_escape", "작업 경로에 심볼릭 링크가 있습니다")
        return path

    async def git(self, cwd: Path, *args: str, cancel: asyncio.Event) -> str:
        """셸·hook·서명 없이 Git을 실행하며 부모 취소를 그대로 전달한다."""
        if cancel.is_set():
            raise asyncio.CancelledError
        options = [
            "git",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "tag.gpgSign=false",
        ]
        env = scrub_env(
            {
                **self._env,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_COUNT": "0",
                "GIT_CONFIG_PARAMETERS": "",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_AUTHOR_NAME": "Perdix",
                "GIT_AUTHOR_EMAIL": "perdix@localhost",
                "GIT_COMMITTER_NAME": "Perdix",
                "GIT_COMMITTER_EMAIL": "perdix@localhost",
            }
        )
        config = await self.backend.run_argv(
            [
                *options,
                "config",
                "--get-regexp",
                r"^(filter\..*|merge\..*\.driver|core\.(sshcommand|gitproxy))$",
            ],
            cwd=cwd,
            timeout=120,
            cancel=cancel,
            env_extra=env,
        )
        if config.cancelled or cancel.is_set():
            raise asyncio.CancelledError
        if config.exit_code != 1 or config.timed_out:
            raise WorktreeError("unsafe_git_config", "외부 명령 Git 설정을 사용할 수 없습니다")
        result = await self.backend.run_argv(
            [*options, *args],
            cwd=cwd,
            timeout=120,
            cancel=cancel,
            env_extra=env,
        )
        if result.cancelled or cancel.is_set():
            raise asyncio.CancelledError
        if result.exit_code != 0 or result.timed_out:
            # Git 출력에 설정 값이나 자격 증명이 포함될 수 있어 공개 오류에 싣지 않는다.
            raise WorktreeError("git_failed", f"Git {args[0]} 실패 (exit={result.exit_code})")
        return result.stdout.rstrip("\n")

    async def prepare(
        self,
        *,
        project_root: Path,
        run_id: str,
        task_id: str,
        attempt_id: str,
        trusted_config_json: str,
        trusted: bool,
        cancel: asyncio.Event,
        base_commit: str | None = None,
    ) -> WorktreeRef:
        if not trusted:
            raise WorktreeError("untrusted", "프로젝트 신뢰가 필요합니다")
        root = project_root.resolve()
        location = self._location(run_id, task_id, attempt_id)
        if self.storage_root.is_relative_to(root) or root.is_relative_to(self.storage_root):
            raise WorktreeError("path_escape", "작업 보관 경로는 원본 저장소 외부여야 합니다")
        actual_root = Path(await self.git(root, "rev-parse", "--show-toplevel", cancel=cancel))
        if actual_root.resolve() != root:
            raise WorktreeError("not_root", "저장소 루트를 지정해야 합니다")
        if await self.git(root, "status", "--porcelain", "--untracked-files=all", cancel=cancel):
            raise WorktreeError("dirty_source", "원본 저장소에 미커밋 변경이 있습니다")
        head = await self.git(root, "rev-parse", "HEAD", cancel=cancel)
        if base_commit is not None and base_commit != head:
            raise WorktreeError("base_changed", "원본 HEAD가 고정 기준 커밋과 다릅니다")
        common = Path(
            await self.git(
                root,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
                cancel=cancel,
            )
        ).resolve()
        ref = WorktreeRef(
            project_root=root,
            common_git_dir=common,
            base_commit=head,
            path=location / "tree",
            branch=f"perdix/{run_id}/{task_id}/{attempt_id}",
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            trusted_config_json=trusted_config_json,
            trusted_config_digest=hashlib.sha256(trusted_config_json.encode()).hexdigest(),
        )
        try:
            location.mkdir(parents=True, exist_ok=False)
            # Git 직전에 등록하여 프로세스 중단 시에도 보존 경로를 찾을 수 있게 한다.
            (location / "registry.json").write_text(ref.model_dump_json(), encoding="utf-8")
        except OSError as exc:
            raise WorktreeError("storage_failed", "작업 경로를 새로 만들 수 없습니다") from exc
        await self.git(
            root,
            "worktree",
            "add",
            "-b",
            ref.branch,
            str(ref.path),
            head,
            cancel=cancel,
        )
        return ref

    async def inspect(self, project_root: Path, *, cancel: asyncio.Event) -> tuple[str, str]:
        root = project_root.resolve()
        actual = await self.git(root, "rev-parse", "--show-toplevel", cancel=cancel)
        if Path(actual).resolve() != root:
            raise WorktreeError("not_root", "저장소 루트를 지정해야 합니다")
        if await self.git(root, "status", "--porcelain", "--untracked-files=all", cancel=cancel):
            raise WorktreeError("dirty_source", "원본 저장소에 미커밋 변경이 있습니다")
        return (
            await self.git(root, "rev-parse", "HEAD", cancel=cancel),
            await self.git(root, "symbolic-ref", "--short", "HEAD", cancel=cancel),
        )

    async def validate(self, ref: WorktreeRef, *, cancel: asyncio.Event) -> None:
        """등록된 원본·Git 공통 경로·기준·설정이 일치하는 파생 경로만 허용한다."""
        location = self._location(ref.run_id, ref.task_id, ref.attempt_id)
        if ref.path != location / "tree" or ref.path.resolve() != ref.path:
            raise WorktreeError("path_escape", "등록 경로를 벗어난 worktree입니다")
        try:
            registered = WorktreeRef.model_validate_json(
                (location / "registry.json").read_text(encoding="utf-8"),
            )
        except (OSError, ValidationError) as exc:
            raise WorktreeError(
                "registry_invalid",
                "worktree 등록 정보를 읽을 수 없습니다",
            ) from exc
        if registered != ref or hashlib.sha256(ref.trusted_config_json.encode()).hexdigest() != (
            ref.trusted_config_digest
        ):
            raise WorktreeError("registry_mismatch", "고정된 worktree 등록 정보가 다릅니다")
        common = Path(
            await self.git(
                ref.path,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
                cancel=cancel,
            )
        ).resolve()
        branch = await self.git(ref.path, "symbolic-ref", "--short", "HEAD", cancel=cancel)
        if common != ref.common_git_dir or branch != ref.branch:
            raise WorktreeError("registry_mismatch", "Git worktree 정체성이 다릅니다")
        await self.git(
            ref.path,
            "merge-base",
            "--is-ancestor",
            ref.base_commit,
            "HEAD",
            cancel=cancel,
        )

    async def collect(self, ref: WorktreeRef, *, cancel: asyncio.Event) -> CandidateCommit:
        await self.validate(ref, cancel=cancel)
        paths = await self.git(
            ref.path,
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            cancel=cancel,
        )
        for name in paths.split("\0"):
            if name and not (ref.path / name).resolve().is_relative_to(ref.path):
                raise WorktreeError("path_escape", "worktree 외부를 가리키는 파일입니다")
        await self.git(ref.path, "add", "--all", "--", ".", cancel=cancel)
        tree = await self.git(ref.path, "write-tree", cancel=cancel)
        previous = await self.git(ref.path, "rev-parse", "HEAD^{tree}", cancel=cancel)
        if tree != previous:
            await self.git(
                ref.path,
                "-c",
                "user.name=Perdix",
                "-c",
                "user.email=perdix@localhost",
                "commit",
                "--no-gpg-sign",
                "-m",
                "Collect workflow task changes",
                cancel=cancel,
            )
        commit = await self.git(ref.path, "rev-parse", "HEAD", cancel=cancel)
        return CandidateCommit(commit=commit, tree=tree, base_commit=ref.base_commit, worktree=ref)

    def preserved(self) -> tuple[WorktreeRef, ...]:
        """자동 정리 없이 보존된 등록 목록을 반환한다."""
        return tuple(
            WorktreeRef.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.storage_root.glob("*/*/*/registry.json"))
        )
