"""도구 프로토콜, 실행 컨텍스트, 입력 스키마 검증."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from jsonschema import Draft202012Validator
from pydantic import BaseModel

from perdix.context.messages import DiffDisplay, DisplayContent, TextDisplay
from perdix.sandbox.base import ExecBackend

if TYPE_CHECKING:
    from perdix.subagents.spawn import SubagentOutcome

ToolKind = Literal["read", "edit", "delete", "move", "search", "execute", "think", "fetch", "other"]

# (preset_id, prompt, model 또는 None) -> SubagentOutcome. None이면 이 실행 맥락에서 스폰 불가.
SpawnSubagent = Callable[[str, str, str | None], Awaitable["SubagentOutcome"]]


__all__ = [
    "DiffDisplay",
    "DisplayContent",
    "SpawnSubagent",
    "TextDisplay",
    "Tool",
    "ToolContext",
    "ToolKind",
    "ToolOutput",
    "tool_error",
    "validate_input",
]


class ToolOutput(BaseModel):
    content: str
    is_error: bool = False
    display: list[DisplayContent] = []


def tool_error(message: str) -> ToolOutput:
    return ToolOutput(content=message, is_error=True)


def _ignore_display(_: DisplayContent) -> None:
    return None


@dataclass
class ToolContext:
    cwd: Path
    sandbox: ExecBackend
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    emit: Callable[[DisplayContent], None] = _ignore_display
    spawn_subagent: SpawnSubagent | None = None


class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]
    kind: ToolKind

    def is_read_only(self, input: dict[str, Any]) -> bool: ...

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput: ...


def validate_input(tool: Tool, input: dict[str, Any]) -> list[str]:
    """스키마 위반 메시지 목록. 비어 있으면 유효. 예외를 던지지 않는다(AGENTS.md 규칙 5)."""
    validator = Draft202012Validator(tool.input_schema)
    messages: list[str] = []
    for error in sorted(validator.iter_errors(input), key=lambda e: e.json_path):
        location = "/".join(str(part) for part in error.absolute_path)
        messages.append(f"{location}: {error.message}" if location else error.message)
    return messages
