"""실행 백엔드 계약. bash·verify·MCP·섀도 git 등 모든 하위 프로세스는 이 계약을 거친다."""

from __future__ import annotations

import asyncio
import fnmatch
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# 하위 프로세스에 절대 넘기지 않는 env 키 패턴(대소문자 무시). AGENTS.md 규칙 12.
SECRET_ENV_PATTERNS: tuple[str, ...] = (
    "API_KEY",
    "*_KEY",
    "*_TOKEN",
    "*AUTH_TOKEN*",
    "*SECRET*",
    "*PASSWORD*",
    "*PASSWD*",
    "*_PWD",
    "*CREDENTIAL*",
    "*_PAT",
    "*_AUTH",
    "*_AUTH_*",
    "*_DSN",
    "DATABASE_URL",
    "PERDIX_SECRET_*",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
)
# 값에 자격 증명이 들어 있을 때만 제거하는 키 패턴(`scheme://user:pass@host`)
CREDENTIAL_URL_PATTERNS: tuple[str, ...] = ("*_URL",)
# 비밀 패턴과 닮았지만 넘겨야 하는 키. SSH_AUTH_SOCK은 ssh를 통한 git에 필요하다.
SAFE_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "TERM",
        "SHELL",
        "TOKENIZERS_PARALLELISM",
        "GIT_AUTHOR_NAME",
        "SSH_AUTH_SOCK",
    }
)
SAFE_ENV_PATTERNS: tuple[str, ...] = ("XDG_*",)


def is_secret_env_key(key: str) -> bool:
    upper = key.upper()
    if upper in SAFE_ENV_KEYS or any(fnmatch.fnmatchcase(upper, p) for p in SAFE_ENV_PATTERNS):
        return False
    return any(fnmatch.fnmatchcase(upper, pattern) for pattern in SECRET_ENV_PATTERNS)


def is_secret_env(key: str, value: str) -> bool:
    """키 패턴, 또는 자격 증명이 든 URL 값으로 비밀 여부를 판단한다."""
    if is_secret_env_key(key):
        return True
    upper = key.upper()
    return (
        any(fnmatch.fnmatchcase(upper, pattern) for pattern in CREDENTIAL_URL_PATTERNS)
        and "://" in value
        and "@" in value
    )


def scrub_env(env: Mapping[str, str]) -> dict[str, str]:
    """비밀로 보이는 키를 제거한 env 사본을 돌려준다."""
    return {key: value for key, value in env.items() if not is_secret_env(key, value)}


@dataclass(frozen=True)
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    cancelled: bool = False


class ExecBackend(Protocol):
    async def run(
        self,
        cmd: str,
        *,
        cwd: Path,
        timeout: float,
        cancel: asyncio.Event,
        env_extra: dict[str, str] | None = None,
    ) -> ExecResult: ...

    async def run_argv(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
        cancel: asyncio.Event,
        env_extra: dict[str, str] | None = None,
    ) -> ExecResult:
        """셸 없이 argv를 실행한다. 프로세스 그룹·취소·env 정리 계약은 `run`과 같다."""
        ...
