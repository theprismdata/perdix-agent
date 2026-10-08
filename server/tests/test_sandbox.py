import asyncio
import os
import time
from pathlib import Path

import pytest

from perdix.sandbox.base import scrub_env
from perdix.sandbox.local import LocalBackend


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_scrub_env_removes_secrets() -> None:
    env = {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "ANTHROPIC_API_KEY": "x",
        "OPENAI_API_KEY": "x",
        "GITHUB_TOKEN": "x",
        "AWS_SECRET_ACCESS_KEY": "x",
        "MY_SECRET": "x",
        "PERDIX_SECRET_FOO": "x",
        "GIT_DIR": "/x",
        "GIT_WORK_TREE": "/x",
        "GIT_INDEX_FILE": "/x",
        "GIT_AUTHOR_NAME": "me",
        "TOKENIZERS_PARALLELISM": "false",
    }
    assert scrub_env(env) == {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "GIT_AUTHOR_NAME": "me",
        "TOKENIZERS_PARALLELISM": "false",
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("API_KEY", "x"),
        ("api_key", "x"),
        ("PGPASSWORD", "x"),
        ("MYSQL_PWD", "x"),
        ("DB_PASSWD", "x"),
        ("AZURE_OPENAI_KEY", "x"),
        ("GITHUB_PAT", "x"),
        ("NPM_AUTH", "x"),
        ("NPM_AUTH_TOKEN", "x"),
        ("SOME_AUTH_HEADER", "x"),
        ("GOOGLE_APPLICATION_CREDENTIALS", "/k.json"),
        ("DATABASE_URL", "postgres://u:p@h/db"),
        ("DATABASE_URL", "sqlite:///x.db"),
        ("SENTRY_DSN", "https://k@sentry.io/1"),
        ("REDIS_URL", "redis://:pw@localhost:6379"),
    ],
)
def test_scrub_env_removes_more_secrets(key: str, value: str) -> None:
    assert scrub_env({key: value, "PATH": "/bin"}) == {"PATH": "/bin"}


def test_scrub_env_keeps_look_alikes() -> None:
    env = {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "LANG": "C.UTF-8",
        "TERM": "xterm",
        "SHELL": "/bin/zsh",
        "TOKENIZERS_PARALLELISM": "false",
        "GIT_AUTHOR_NAME": "me",
        "SSH_AUTH_SOCK": "/tmp/agent.sock",
        "XDG_CONFIG_HOME": "/home/u/.config",
        "XDG_RUNTIME_DIR": "/run/user/1",
        "PWD": "/home/u",
        "OLDPWD": "/",
        # 자격 증명 없는 URL은 남긴다
        "HOMEBREW_BOTTLE_DOMAIN_URL": "https://ghcr.io/v2",
        "PIP_INDEX_URL": "https://pypi.org/simple",
    }
    assert scrub_env(env) == env


async def test_subprocess_gets_scrubbed_env_plus_extra(tmp_path: Path) -> None:
    backend = LocalBackend(
        base_env={"PATH": os.environ["PATH"], "ANTHROPIC_API_KEY": "sk-leak", "KEEP": "yes"}
    )
    result = await backend.run(
        "env",
        cwd=tmp_path,
        timeout=10,
        cancel=asyncio.Event(),
        env_extra={"EXTRA_VAR": "1"},
    )
    assert result.exit_code == 0
    assert "sk-leak" not in result.stdout
    assert "ANTHROPIC_API_KEY" not in result.stdout
    assert "KEEP=yes" in result.stdout
    assert "EXTRA_VAR=1" in result.stdout


async def test_default_env_is_scrubbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_SERVICE_API_KEY", "sk-leak")
    result = await LocalBackend().run(
        "env", cwd=tmp_path, timeout=10, cancel=asyncio.Event()
    )
    assert "sk-leak" not in result.stdout


async def test_run_captures_output_and_exit_code(tmp_path: Path) -> None:
    result = await LocalBackend().run(
        "echo out; echo err >&2; exit 3", cwd=tmp_path, timeout=10, cancel=asyncio.Event()
    )
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"
    assert result.exit_code == 3
    assert not result.timed_out


async def test_runs_in_cwd(tmp_path: Path) -> None:
    result = await LocalBackend().run("pwd", cwd=tmp_path, timeout=10, cancel=asyncio.Event())
    assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()


async def test_timeout_kills_process_group(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    start = time.monotonic()
    result = await LocalBackend().run(
        f"sh -c 'sleep 30 & echo $! > {pidfile}; sleep 30'",
        cwd=tmp_path,
        timeout=0.5,
        cancel=asyncio.Event(),
    )
    assert result.timed_out
    assert time.monotonic() - start < 10
    child = int(pidfile.read_text().strip())
    assert _wait_dead(child), "background child survived the timeout kill"


async def test_cancel_kills_whole_process_group(tmp_path: Path) -> None:
    pidfile = tmp_path / "child.pid"
    cancel = asyncio.Event()
    backend = LocalBackend()
    task = asyncio.create_task(
        backend.run(
            f"sh -c 'sleep 30 & echo $! > {pidfile}; sleep 30'",
            cwd=tmp_path,
            timeout=60,
            cancel=cancel,
        )
    )
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        await asyncio.sleep(0.05)
    child = int(pidfile.read_text().strip())
    assert _pid_alive(child)
    start = time.monotonic()
    cancel.set()
    result = await task
    assert result.cancelled
    assert not result.timed_out
    assert time.monotonic() - start < 10
    assert _wait_dead(child), "background child survived cancel"


async def test_sigterm_ignoring_process_gets_sigkilled(tmp_path: Path) -> None:
    pidfile = tmp_path / "pid"
    start = time.monotonic()
    result = await LocalBackend().run(
        f"sh -c 'trap \"\" TERM; echo $$ > {pidfile}; while true; do sleep 0.1; done'",
        cwd=tmp_path,
        timeout=0.5,
        cancel=asyncio.Event(),
    )
    assert result.timed_out
    elapsed = time.monotonic() - start
    assert 2.0 <= elapsed < 10
    assert _wait_dead(int(pidfile.read_text().strip()))


async def test_run_argv_captures_output_without_shell(tmp_path: Path) -> None:
    result = await LocalBackend().run_argv(
        ["echo", "$HOME;", "x"], cwd=tmp_path, timeout=10, cancel=asyncio.Event()
    )
    assert result.stdout == "$HOME; x\n"
    assert result.exit_code == 0


async def test_run_argv_env_is_scrubbed(tmp_path: Path) -> None:
    backend = LocalBackend(base_env={"PATH": os.environ["PATH"], "PGPASSWORD": "pw-leak"})
    result = await backend.run_argv(["env"], cwd=tmp_path, timeout=10, cancel=asyncio.Event())
    assert "pw-leak" not in result.stdout


async def test_run_argv_cancel_kills_process(tmp_path: Path) -> None:
    pidfile = tmp_path / "pid"
    cancel = asyncio.Event()
    task = asyncio.create_task(
        LocalBackend().run_argv(
            ["sh", "-c", f"echo $$ > {pidfile}; exec sleep 30"],
            cwd=tmp_path,
            timeout=60,
            cancel=cancel,
        )
    )
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        await asyncio.sleep(0.05)
    pid = int(pidfile.read_text().strip())
    assert _pid_alive(pid)
    start = time.monotonic()
    cancel.set()
    result = await task
    assert result.cancelled
    assert time.monotonic() - start < 10
    assert _wait_dead(pid), "argv process survived cancel"
