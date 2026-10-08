import asyncio
import os
import shutil
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from perdix.sandbox.base import ExecResult
from perdix.sandbox.local import LocalBackend
from perdix.tools import search
from perdix.tools.base import DiffDisplay, ToolContext, ToolOutput, validate_input
from perdix.tools.fs import EditTool, GlobTool, LsTool, ReadTool, WriteTool
from perdix.tools.registry import ToolRegistry, builtin_tools
from perdix.tools.search import GrepTool
from perdix.tools.shell import BashTool, keep_tail


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("alpha\nbeta\ngamma\n")
    (root / "src").mkdir()
    (root / "src" / "m.py").write_text("def foo():\n    return 1\n")
    (root / "src" / "n.py").write_text("x = 'foo'\n")
    (tmp_path / "outside.txt").write_text("secret\n")
    return root


@pytest.fixture
def ctx(workdir: Path) -> ToolContext:
    return ToolContext(cwd=workdir, sandbox=LocalBackend(), cancel=asyncio.Event())


async def run(tool: Any, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
    assert validate_input(tool, input) == [], validate_input(tool, input)
    out: ToolOutput = await tool.run(ctx, input)
    return out


# --- validate_input / registry ---


def test_validate_input_reports_errors_without_raising() -> None:
    errors = validate_input(ReadTool(), {"offset": "x", "bogus": 1})
    joined = "\n".join(errors)
    assert "'path' is a required property" in joined
    assert "offset" in joined
    assert "bogus" in joined
    assert validate_input(ReadTool(), {"path": "a.txt"}) == []


def test_registry_specs_and_read_only() -> None:
    registry = ToolRegistry(builtin_tools())
    names = {s.name for s in registry.specs()}
    assert names == {"read", "write", "edit", "glob", "grep", "ls", "bash", "task"}
    for spec in registry.specs():
        assert spec.input_schema["type"] == "object"
        assert spec.description
    ro = registry.read_only_only()
    assert set(ro.names()) == {"read", "glob", "grep", "ls"}
    assert registry.get("bash") is not None
    assert ro.get("bash") is None
    with pytest.raises(ValueError):
        ToolRegistry([ReadTool(), ReadTool()])


def test_is_read_only_flags() -> None:
    for tool in (ReadTool(), GlobTool(), GrepTool(), LsTool()):
        assert tool.is_read_only({})
    for tool in (WriteTool(), EditTool()):
        assert not tool.is_read_only({})
    assert not BashTool().is_read_only({"command": "ls"})
    assert BashTool().kind == "execute"


# --- read ---


async def test_read_line_numbers(ctx: ToolContext) -> None:
    out = await run(ReadTool(), ctx, {"path": "a.txt"})
    assert not out.is_error
    assert out.content.splitlines() == ["     1\talpha", "     2\tbeta", "     3\tgamma"]


async def test_read_offset_limit(ctx: ToolContext) -> None:
    out = await run(ReadTool(), ctx, {"path": "a.txt", "offset": 2, "limit": 1})
    lines = out.content.splitlines()
    assert lines[0] == "     2\tbeta"
    assert "offset=3" in lines[-1]


async def test_read_line_cap(ctx: ToolContext, workdir: Path) -> None:
    (workdir / "big.txt").write_text("".join(f"l{i}\n" for i in range(3000)))
    out = await run(ReadTool(), ctx, {"path": "big.txt"})
    lines = out.content.splitlines()
    assert len(lines) == 2001
    assert "offset=2001" in lines[-1]


async def test_read_byte_cap(ctx: ToolContext, workdir: Path) -> None:
    (workdir / "wide.txt").write_text(("x" * 1000 + "\n") * 200)
    out = await run(ReadTool(), ctx, {"path": "wide.txt"})
    assert len(out.content.encode()) <= 50_000 + 200
    assert "truncated" in out.content.splitlines()[-1]


async def test_read_errors(ctx: ToolContext, workdir: Path) -> None:
    assert (await run(ReadTool(), ctx, {"path": "nope.txt"})).is_error
    (workdir / "bin").write_bytes(b"\x00\x01\x02")
    assert (await run(ReadTool(), ctx, {"path": "bin"})).is_error
    assert (await run(ReadTool(), ctx, {"path": "a.txt", "offset": 99})).is_error


# --- path escape ---


@pytest.mark.parametrize(
    "tool,extra",
    [
        (ReadTool(), {}),
        (WriteTool(), {"content": "pwned"}),
        (EditTool(), {"old_string": "secret", "new_string": "x"}),
        (LsTool(), {}),
        (GrepTool(), {"pattern": "secret"}),
        (GlobTool(), {"pattern": "*"}),
    ],
)
@pytest.mark.parametrize("kind", ["dotdot", "absolute", "symlink"])
async def test_path_escape_rejected(
    ctx: ToolContext, workdir: Path, tool: Any, extra: dict[str, Any], kind: str
) -> None:
    outside = workdir.parent / "outside.txt"
    if kind == "dotdot":
        target = "../outside.txt"
    elif kind == "absolute":
        target = str(outside)
    else:
        (workdir / "link").symlink_to(outside)
        target = "link"
    if isinstance(tool, (LsTool, GlobTool)):
        # 디렉터리 인자를 받는 도구는 바깥 디렉터리를 가리키게 한다
        if kind == "symlink":
            (workdir / "dirlink").symlink_to(workdir.parent)
            target = "dirlink"
        else:
            target = str(workdir.parent) if kind == "absolute" else ".."
    out = await tool.run(ctx, {"path": target, **extra})
    assert out.is_error
    assert "outside the working directory" in out.content
    assert outside.read_text() == "secret\n"


async def test_glob_does_not_follow_symlink_out(ctx: ToolContext, workdir: Path) -> None:
    (workdir / "link.txt").symlink_to(workdir.parent / "outside.txt")
    out = await run(GlobTool(), ctx, {"pattern": "*.txt"})
    assert out.content.splitlines() == ["a.txt"]


# --- write / edit ---


async def test_write_creates_and_overwrites(ctx: ToolContext, workdir: Path) -> None:
    out = await run(WriteTool(), ctx, {"path": "new/dir/f.txt", "content": "hi\n"})
    assert not out.is_error
    assert (workdir / "new/dir/f.txt").read_text() == "hi\n"
    assert out.display == [
        DiffDisplay(
            path=str((workdir / "new/dir/f.txt").resolve()), old_text=None, new_text="hi\n"
        )
    ]
    assert out.content.startswith("created new/dir/f.txt")  # 모델용 텍스트는 상대 경로
    out = await run(WriteTool(), ctx, {"path": "a.txt", "content": "z"})
    assert isinstance(out.display[0], DiffDisplay)
    assert out.display[0].old_text == "alpha\nbeta\ngamma\n"
    assert (workdir / "a.txt").read_text() == "z"


async def test_write_to_directory_is_error(ctx: ToolContext) -> None:
    assert (await run(WriteTool(), ctx, {"path": "src", "content": "x"})).is_error


async def test_edit_unique(ctx: ToolContext, workdir: Path) -> None:
    out = await run(
        EditTool(), ctx, {"path": "a.txt", "old_string": "beta", "new_string": "BETA"}
    )
    assert not out.is_error
    assert (workdir / "a.txt").read_text() == "alpha\nBETA\ngamma\n"
    assert out.display == [
        DiffDisplay(
            path=str((workdir / "a.txt").resolve()),
            old_text="alpha\nbeta\ngamma\n",
            new_text="alpha\nBETA\ngamma\n",
        )
    ]


async def test_edit_requires_uniqueness(ctx: ToolContext, workdir: Path) -> None:
    (workdir / "d.txt").write_text("x x x")
    out = await run(EditTool(), ctx, {"path": "d.txt", "old_string": "x", "new_string": "y"})
    assert out.is_error
    assert "3 times" in out.content
    assert (workdir / "d.txt").read_text() == "x x x"
    out = await run(
        EditTool(),
        ctx,
        {"path": "d.txt", "old_string": "x", "new_string": "y", "replace_all": True},
    )
    assert not out.is_error
    assert (workdir / "d.txt").read_text() == "y y y"


async def test_edit_errors(ctx: ToolContext) -> None:
    edit = EditTool()
    missing = {"path": "a.txt", "old_string": "zzz", "new_string": "q"}
    assert (await run(edit, ctx, missing)).is_error
    assert (await run(edit, ctx, {"path": "a.txt", "old_string": "a", "new_string": "a"})).is_error
    assert (await run(edit, ctx, {"path": "no.txt", "old_string": "a", "new_string": "b"})).is_error
    assert validate_input(edit, {"path": "a.txt", "old_string": "", "new_string": "b"})


# --- glob / ls ---


async def test_glob(ctx: ToolContext) -> None:
    out = await run(GlobTool(), ctx, {"pattern": "**/*.py"})
    assert out.content.splitlines() == ["src/m.py", "src/n.py"]
    out = await run(GlobTool(), ctx, {"pattern": "*.py", "path": "src"})
    assert out.content.splitlines() == ["src/m.py", "src/n.py"]
    out = await run(GlobTool(), ctx, {"pattern": "*.rs"})
    assert out.content == "no files matched"
    assert (await run(GlobTool(), ctx, {"pattern": "*", "path": "a.txt"})).is_error


async def test_ls(ctx: ToolContext) -> None:
    out = await run(LsTool(), ctx, {})
    assert out.content.splitlines() == ["a.txt", "src/"]
    out = await run(LsTool(), ctx, {"path": "src"})
    assert out.content.splitlines() == ["m.py", "n.py"]
    assert (await run(LsTool(), ctx, {"path": "a.txt"})).is_error


# --- grep ---


@pytest.fixture(params=["ripgrep", "python"])
def grep_impl(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    impl: str = request.param
    if impl == "ripgrep":
        if shutil.which("rg") is None:
            pytest.fail("ripgrep (rg) must be installed to run the ripgrep path tests")
    else:
        monkeypatch.setattr(search.shutil, "which", lambda _name: None)
    return impl


async def test_grep_matches(ctx: ToolContext, grep_impl: str) -> None:
    out = await run(GrepTool(), ctx, {"pattern": "foo"})
    assert not out.is_error
    assert sorted(out.content.splitlines()) == ["src/m.py:1:def foo():", "src/n.py:1:x = 'foo'"]


async def test_grep_path_and_glob(ctx: ToolContext, workdir: Path, grep_impl: str) -> None:
    (workdir / "src" / "o.txt").write_text("foo here\n")
    out = await run(GrepTool(), ctx, {"pattern": "foo", "path": "src", "glob": "*.txt"})
    assert out.content.splitlines() == ["src/o.txt:1:foo here"]
    out = await run(GrepTool(), ctx, {"pattern": "fo+", "path": "src/m.py"})
    assert out.content.splitlines() == ["src/m.py:1:def foo():"]


async def test_grep_no_match_and_errors(ctx: ToolContext, grep_impl: str) -> None:
    out = await run(GrepTool(), ctx, {"pattern": "zzzz"})
    assert out.content == "no matches"
    assert not out.is_error
    assert (await run(GrepTool(), ctx, {"pattern": "(unclosed"})).is_error
    assert (await run(GrepTool(), ctx, {"pattern": "x", "path": "missing"})).is_error


async def test_grep_result_cap(ctx: ToolContext, workdir: Path, grep_impl: str) -> None:
    (workdir / "many.txt").write_text("hit\n" * 2000)
    out = await run(GrepTool(), ctx, {"pattern": "hit"})
    lines = out.content.splitlines()
    assert len(lines) == 501
    assert "truncated" in lines[-1]


# --- bash ---


async def test_bash_ok(ctx: ToolContext) -> None:
    out = await run(BashTool(), ctx, {"command": "echo hello && pwd"})
    assert not out.is_error
    assert out.content.splitlines()[0] == "hello"
    assert Path(out.content.splitlines()[1]).resolve() == ctx.cwd.resolve()


async def test_bash_nonzero_exit(ctx: ToolContext) -> None:
    out = await run(BashTool(), ctx, {"command": "echo bad >&2; exit 2"})
    assert out.is_error
    assert "bad" in out.content
    assert "exit code 2" in out.content


async def test_bash_timeout(ctx: ToolContext) -> None:
    out = await run(BashTool(), ctx, {"command": "sleep 30", "timeout": 0.3})
    assert out.is_error
    assert "timed out" in out.content


async def test_bash_output_tail(ctx: ToolContext) -> None:
    out = await run(
        BashTool(), ctx, {"command": "i=0; while [ $i -lt 5000 ]; do echo line$i; i=$((i+1)); done"}
    )
    assert out.content.startswith("[output truncated")
    assert out.content.rstrip().endswith("line4999")
    assert len(out.content.encode()) <= 30_000 + 100


async def test_bash_env_scrubbed(ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEAKY_API_KEY", "sk-should-not-leak")
    out = await run(BashTool(), ctx, {"command": "env"})
    assert "sk-should-not-leak" not in out.content


async def test_bash_uses_ctx_sandbox(workdir: Path) -> None:
    from perdix.sandbox.base import ExecResult

    calls: list[tuple[str, Path, float]] = []

    class FakeBackend:
        async def run(
            self,
            cmd: str,
            *,
            cwd: Path,
            timeout: float,
            cancel: asyncio.Event,
            env_extra: dict[str, str] | None = None,
        ) -> ExecResult:
            calls.append((cmd, cwd, timeout))
            return ExecResult(stdout="fake\n", stderr="", exit_code=0)

    ctx = ToolContext(cwd=workdir, sandbox=FakeBackend())
    out = await BashTool().run(ctx, {"command": "anything"})
    assert out.content == "fake"
    assert calls == [("anything", workdir, 120.0)]


async def test_grep_ripgrep_goes_through_sandbox_and_honours_cancel(
    workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rg는 ctx.sandbox.run_argv로 실행되고 ctx.cancel에 프로세스 그룹째 회수된다."""
    seen: list[list[str]] = []
    pidfile = workdir.parent / "rg.pid"

    class StandInBackend(LocalBackend):
        async def run_argv(
            self,
            argv: Sequence[str],
            *,
            cwd: Path,
            timeout: float,
            cancel: asyncio.Event,
            env_extra: dict[str, str] | None = None,
        ) -> ExecResult:
            seen.append(list(argv))
            # 오래 걸리는 rg 대역
            stand_in = ["sh", "-c", f"echo $$ > {pidfile}; exec sleep 30"]
            return await super().run_argv(
                stand_in, cwd=cwd, timeout=timeout, cancel=cancel, env_extra=env_extra
            )

    monkeypatch.setattr(search.shutil, "which", lambda name: "/fake/rg")
    cancel = asyncio.Event()
    ctx = ToolContext(cwd=workdir, sandbox=StandInBackend(), cancel=cancel)
    task = asyncio.create_task(GrepTool().run(ctx, {"pattern": "foo"}))
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        await asyncio.sleep(0.05)
    pid = int(pidfile.read_text().strip())
    cancel.set()
    out = await asyncio.wait_for(task, timeout=10)
    assert out.is_error and out.content == "grep cancelled"
    assert seen and seen[0][0] == "/fake/rg"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("grep subprocess survived cancel")


def test_keep_tail() -> None:
    assert keep_tail("abc", 10) == "abc"
    truncated = keep_tail("0123456789", 4)
    assert truncated.endswith("6789")
    assert "6 bytes omitted" in truncated
