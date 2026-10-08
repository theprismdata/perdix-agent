"""grep 도구. ripgrep이 PATH에 있으면 쓰고, 없으면 Python으로 폴백한다."""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import shutil
from pathlib import Path
from typing import Any

from perdix.tools.base import ToolContext, ToolKind, ToolOutput, tool_error
from perdix.tools.fs import PathEscapeError, resolve_in_cwd

GREP_MAX_MATCHES = 500
GREP_MAX_BYTES = 30_000
GREP_TIMEOUT_SECONDS = 60.0
# Python 폴백에서 건너뛰는 디렉터리(ripgrep은 .gitignore와 숨김 규칙을 따른다)
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv"}


def _cap(lines: list[str]) -> str:
    out: list[str] = []
    size = 0
    for count, line in enumerate(lines):
        if count >= GREP_MAX_MATCHES or size + len(line) + 1 > GREP_MAX_BYTES:
            out.append(f"[truncated: showing {count} of {len(lines)} matching lines]")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


async def _ripgrep(
    rg: str, ctx: ToolContext, pattern: str, target: Path, glob: str | None
) -> ToolOutput:
    root = ctx.cwd.resolve()
    args = [rg, "-n", "--with-filename", "--no-heading", "--color", "never", "--no-messages"]
    if glob:
        args += ["--glob", glob]
    rel = os.path.relpath(target, root)
    args += ["-e", pattern, "--", rel]
    result = await ctx.sandbox.run_argv(
        args, cwd=root, timeout=GREP_TIMEOUT_SECONDS, cancel=ctx.cancel
    )
    if result.cancelled:
        return tool_error("grep cancelled")
    if result.timed_out:
        return tool_error(f"grep timed out after {GREP_TIMEOUT_SECONDS:.0f}s")
    if result.exit_code == 1:
        return ToolOutput(content="no matches")
    if result.exit_code != 0:
        return tool_error(f"grep failed: {result.stderr.strip()}")
    lines = result.stdout.splitlines()
    lines = [line[2:] if line.startswith("./") else line for line in lines]
    return ToolOutput(content=_cap(lines))


def _python_grep(ctx: ToolContext, pattern: str, target: Path, glob: str | None) -> ToolOutput:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return tool_error(f"invalid regex: {exc}")
    root = ctx.cwd.resolve()

    def files() -> list[Path]:
        if target.is_file():
            return [target]
        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(target, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for filename in sorted(filenames):
                if glob and not (
                    fnmatch.fnmatch(filename, glob)
                    or fnmatch.fnmatch(
                        Path(dirpath, filename).relative_to(root).as_posix(), glob
                    )
                ):
                    continue
                found.append(Path(dirpath, filename))
        return found

    lines: list[str] = []
    for path in files():
        if path.is_symlink() and not path.resolve().is_relative_to(root):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:8192]:
            continue
        rel = path.relative_to(root).as_posix()
        for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if regex.search(line):
                lines.append(f"{rel}:{number}:{line}")
    if not lines:
        return ToolOutput(content="no matches")
    return ToolOutput(content=_cap(lines))


class GrepTool:
    name = "grep"
    description = (
        "Search file contents with a regular expression. Returns path:line:text. "
        "Optional path (file or directory) and glob filter (e.g. '*.py')."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "minLength": 1},
            "path": {"type": "string"},
            "glob": {"type": "string"},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }
    kind: ToolKind = "search"

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return True

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        raw = input.get("path", ".")
        try:
            target = resolve_in_cwd(ctx, raw)
        except PathEscapeError as exc:
            return tool_error(str(exc))
        if not target.exists():
            return tool_error(f"path not found: {raw}")
        pattern: str = input["pattern"]
        glob: str | None = input.get("glob")
        rg = shutil.which("rg")
        if rg is not None:
            return await _ripgrep(rg, ctx, pattern, target, glob)
        return await asyncio.to_thread(_python_grep, ctx, pattern, target, glob)
