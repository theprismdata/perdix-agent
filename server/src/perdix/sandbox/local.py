"""로컬 셸 실행 백엔드. 새 세션(프로세스 그룹)으로 띄워 취소 시 그룹 전체를 죽인다."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from collections.abc import Sequence
from pathlib import Path

from perdix.sandbox.base import ExecResult, scrub_env

# SIGTERM 후 SIGKILL까지 기다리는 시간(초)
KILL_GRACE_SECONDS = 2.0
# 그룹을 죽인 뒤 파이프가 닫히길 기다리는 상한(초). 그룹을 탈출한 프로세스가 파이프를 쥔 경우 대비.
DRAIN_TIMEOUT_SECONDS = 5.0


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _kill_group(pgid: int) -> None:
    """프로세스 그룹에 SIGTERM, 유예 후에도 남아 있으면 SIGKILL."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + KILL_GRACE_SECONDS
    while loop.time() < deadline:
        if not _group_alive(pgid):
            return
        await asyncio.sleep(0.05)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL)


class LocalBackend:
    def __init__(self, base_env: dict[str, str] | None = None) -> None:
        # base_env를 주지 않으면 서버 프로세스 env를 쓴다. 어느 쪽이든 항상 scrub한다.
        self._base_env = base_env

    def _env(self, env_extra: dict[str, str] | None) -> dict[str, str]:
        env = scrub_env(self._base_env if self._base_env is not None else os.environ)
        if env_extra:
            env.update(env_extra)
        return env

    async def run(
        self,
        cmd: str,
        *,
        cwd: Path,
        timeout: float,
        cancel: asyncio.Event,
        env_extra: dict[str, str] | None = None,
    ) -> ExecResult:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(cwd),
            env=self._env(env_extra),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        return await _collect(proc, timeout, cancel)

    async def run_argv(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
        cancel: asyncio.Event,
        env_extra: dict[str, str] | None = None,
    ) -> ExecResult:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=self._env(env_extra),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        return await _collect(proc, timeout, cancel)


async def _collect(
    proc: asyncio.subprocess.Process, timeout: float, cancel: asyncio.Event
) -> ExecResult:
    """출력을 모은다. 시간 초과·취소면 프로세스 그룹 전체를 죽이고 회수한다."""
    pgid = proc.pid  # start_new_session=True 이므로 pid == pgid
    communicate = asyncio.ensure_future(proc.communicate())
    cancel_wait = asyncio.ensure_future(cancel.wait())
    timed_out = False
    cancelled = False
    try:
        done, _ = await asyncio.wait(
            {communicate, cancel_wait},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if communicate not in done:
            cancelled = cancel_wait in done
            timed_out = not cancelled
            await _kill_group(pgid)
    except asyncio.CancelledError:
        await _kill_group(pgid)
        communicate.cancel()
        raise
    finally:
        cancel_wait.cancel()

    try:
        stdout_b, stderr_b = await asyncio.wait_for(communicate, DRAIN_TIMEOUT_SECONDS)
    except TimeoutError:
        stdout_b, stderr_b = b"", b""
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    exit_code = proc.returncode if proc.returncode is not None else -1
    return ExecResult(
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
        exit_code=exit_code,
        timed_out=timed_out,
        cancelled=cancelled,
    )
