"""파일 시스템 도구: read, write, edit, glob, ls. 모든 경로는 ctx.cwd 안으로 제한한다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from perdix.tools.base import DiffDisplay, ToolContext, ToolKind, ToolOutput, tool_error

READ_MAX_LINES = 2000
READ_MAX_BYTES = 50_000
LIST_MAX_ENTRIES = 1000


class PathEscapeError(ValueError):
    pass


def resolve_in_cwd(ctx: ToolContext, raw: str) -> Path:
    """raw를 cwd 기준으로 resolve한다. 심링크를 따라간 결과가 cwd 밖이면 PathEscapeError."""
    root = ctx.cwd.resolve()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise PathEscapeError(f"path is outside the working directory: {raw}")
    return resolved


def display_path(ctx: ToolContext, path: Path) -> str:
    root = ctx.cwd.resolve()
    rel = path.relative_to(root)
    return rel.as_posix() if rel.parts else "."


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


class ReadTool:
    name = "read"
    description = (
        "Read a text file. Returns lines prefixed with 1-based line numbers. "
        f"At most {READ_MAX_LINES} lines / {READ_MAX_BYTES} bytes per call; "
        "use offset/limit to page."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to the working dir"},
            "offset": {"type": "integer", "minimum": 1, "description": "1-based start line"},
            "limit": {"type": "integer", "minimum": 1, "description": "Number of lines"},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    kind: ToolKind = "read"

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return True

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        try:
            path = resolve_in_cwd(ctx, input["path"])
        except PathEscapeError as exc:
            return tool_error(str(exc))
        if not path.is_file():
            return tool_error(f"file not found: {input['path']}")
        try:
            data = path.read_bytes()
        except OSError as exc:
            return tool_error(f"cannot read {input['path']}: {exc}")
        if _looks_binary(data):
            return tool_error(f"binary file, not shown: {input['path']}")

        lines = data.decode("utf-8", errors="replace").splitlines()
        offset = int(input.get("offset", 1))
        limit = min(int(input.get("limit", READ_MAX_LINES)), READ_MAX_LINES)
        if lines and offset > len(lines):
            return tool_error(f"offset {offset} is beyond end of file ({len(lines)} lines)")

        out: list[str] = []
        size = 0
        end = min(offset - 1 + limit, len(lines))
        last = offset - 1
        for index in range(offset - 1, end):
            row = f"{index + 1:>6}\t{lines[index]}"
            size += len(row.encode("utf-8")) + 1
            if size > READ_MAX_BYTES:
                break
            out.append(row)
            last = index + 1
        if last < len(lines):
            out.append(
                f"[truncated: showed lines {offset}-{last} of {len(lines)}; "
                f"continue with offset={last + 1}]"
            )
        return ToolOutput(content="\n".join(out))


class WriteTool:
    name = "write"
    description = "Create or overwrite a file with the given content."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }
    kind: ToolKind = "edit"

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        try:
            path = resolve_in_cwd(ctx, input["path"])
        except PathEscapeError as exc:
            return tool_error(str(exc))
        if path.is_dir():
            return tool_error(f"path is a directory: {input['path']}")
        content: str = input["content"]
        try:
            old_text = path.read_text(encoding="utf-8") if path.exists() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return tool_error(f"cannot write {input['path']}: {exc}")
        rel = display_path(ctx, path)
        verb = "created" if old_text is None else "overwrote"
        return ToolOutput(
            content=f"{verb} {rel} ({len(content.encode('utf-8'))} bytes)",
            display=[DiffDisplay(path=str(path), old_text=old_text, new_text=content)],
        )


class EditTool:
    name = "edit"
    description = (
        "Replace old_string with new_string in a file. old_string must match exactly once "
        "unless replace_all is true."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string", "minLength": 1},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["path", "old_string", "new_string"],
        "additionalProperties": False,
    }
    kind: ToolKind = "edit"

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        try:
            path = resolve_in_cwd(ctx, input["path"])
        except PathEscapeError as exc:
            return tool_error(str(exc))
        if not path.is_file():
            return tool_error(f"file not found: {input['path']}")
        old_string: str = input["old_string"]
        new_string: str = input["new_string"]
        replace_all = bool(input.get("replace_all", False))
        if old_string == new_string:
            return tool_error("old_string and new_string are identical")
        try:
            old_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return tool_error(f"cannot read {input['path']}: {exc}")
        count = old_text.count(old_string)
        if count == 0:
            return tool_error(f"old_string not found in {input['path']}")
        if count > 1 and not replace_all:
            return tool_error(
                f"old_string matches {count} times in {input['path']}; "
                "add surrounding context to make it unique or set replace_all"
            )
        new_text = old_text.replace(old_string, new_string)
        try:
            path.write_text(new_text, encoding="utf-8")
        except OSError as exc:
            return tool_error(f"cannot write {input['path']}: {exc}")
        rel = display_path(ctx, path)
        return ToolOutput(
            content=f"edited {rel} ({count} replacement{'s' if count > 1 else ''})",
            display=[DiffDisplay(path=str(path), old_text=old_text, new_text=new_text)],
        )


class GlobTool:
    name = "glob"
    description = "Find files matching a glob pattern (supports **). Returns paths relative to cwd."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "minLength": 1},
            "path": {"type": "string", "description": "Directory to search from (default cwd)"},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }
    kind: ToolKind = "search"

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return True

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        try:
            base = resolve_in_cwd(ctx, input.get("path", "."))
        except PathEscapeError as exc:
            return tool_error(str(exc))
        if not base.is_dir():
            return tool_error(f"not a directory: {input.get('path', '.')}")
        root = ctx.cwd.resolve()
        matches: list[str] = []
        truncated = False
        try:
            for found in base.glob(input["pattern"]):
                if not found.is_file():
                    continue
                resolved = found.resolve()
                if not resolved.is_relative_to(root):
                    continue
                if len(matches) >= LIST_MAX_ENTRIES:
                    truncated = True
                    break
                matches.append(resolved.relative_to(root).as_posix())
        except (ValueError, NotImplementedError, OSError) as exc:
            return tool_error(f"invalid glob pattern: {exc}")
        matches.sort()
        if not matches:
            return ToolOutput(content="no files matched")
        if truncated:
            matches.append(f"[truncated at {LIST_MAX_ENTRIES} results]")
        return ToolOutput(content="\n".join(matches))


class LsTool:
    name = "ls"
    description = "List a directory. Directories end with '/'."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "additionalProperties": False,
    }
    kind: ToolKind = "read"

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return True

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        raw = input.get("path", ".")
        try:
            path = resolve_in_cwd(ctx, raw)
        except PathEscapeError as exc:
            return tool_error(str(exc))
        if not path.is_dir():
            return tool_error(f"not a directory: {raw}")
        try:
            entries = sorted(path.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            return tool_error(f"cannot list {raw}: {exc}")
        names = [f"{p.name}/" if p.is_dir() else p.name for p in entries[:LIST_MAX_ENTRIES]]
        if len(entries) > LIST_MAX_ENTRIES:
            names.append(f"[truncated: {len(entries) - LIST_MAX_ENTRIES} more entries]")
        return ToolOutput(content="\n".join(names) if names else "(empty directory)")
