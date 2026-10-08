"""bash 도구. 항상 ctx.sandbox(ExecBackend)를 거쳐 실행한다."""

from __future__ import annotations

from typing import Any

from perdix.tools.base import TextDisplay, ToolContext, ToolKind, ToolOutput

DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_TIMEOUT_SECONDS = 600.0
OUTPUT_MAX_BYTES = 30_000


def keep_tail(text: str, limit: int = OUTPUT_MAX_BYTES) -> str:
    """limit 바이트를 넘으면 앞을 버리고 꼬리를 남긴다."""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    tail = data[-limit:].decode("utf-8", errors="ignore")
    return f"[output truncated: first {len(data) - limit} bytes omitted]\n{tail}"


class BashTool:
    name = "bash"
    description = (
        "Run a shell command in the working directory. "
        f"timeout in seconds (default {DEFAULT_TIMEOUT_SECONDS:.0f}, "
        f"max {MAX_TIMEOUT_SECONDS:.0f}). "
        f"Output over {OUTPUT_MAX_BYTES} bytes keeps only the tail."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "minLength": 1},
            "timeout": {"type": "number", "exclusiveMinimum": 0, "maximum": MAX_TIMEOUT_SECONDS},
        },
        "required": ["command"],
        "additionalProperties": False,
    }
    kind: ToolKind = "execute"

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        timeout = float(input.get("timeout", DEFAULT_TIMEOUT_SECONDS))
        result = await ctx.sandbox.run(
            input["command"], cwd=ctx.cwd, timeout=timeout, cancel=ctx.cancel
        )
        parts: list[str] = []
        if result.stdout:
            parts.append(result.stdout.rstrip("\n"))
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr.rstrip(chr(10))}")
        if result.timed_out:
            parts.append(f"[timed out after {timeout:g}s; process group killed]")
        elif result.cancelled:
            parts.append("[cancelled; process group killed]")
        elif result.exit_code != 0:
            parts.append(f"[exit code {result.exit_code}]")
        text = keep_tail("\n".join(parts)) if parts else "(no output)"
        is_error = result.timed_out or result.cancelled or result.exit_code != 0
        return ToolOutput(content=text, is_error=is_error, display=[TextDisplay(text=text)])
