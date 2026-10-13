"""도구 레지스트리."""

from __future__ import annotations

from collections.abc import Iterable

from perdix.llm.base import ToolSpec
from perdix.tools.base import Tool, ToolKind
from perdix.tools.fs import EditTool, GlobTool, LsTool, ReadTool, WriteTool
from perdix.tools.memory import MemoryAppendTool, MemoryReadTool
from perdix.tools.search import GrepTool
from perdix.tools.session_search import SessionSearchTool
from perdix.tools.shell import BashTool
from perdix.tools.task import TaskTool

# plan 모드 등 읽기 전용 레지스트리에 남기는 kind
READ_ONLY_KINDS: frozenset[ToolKind] = frozenset({"read", "search", "think", "fetch"})


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool]) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"duplicate tool name: {tool.name}")
            self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(name=t.name, description=t.description, input_schema=t.input_schema)
            for t in self._tools.values()
        ]

    def read_only_only(self) -> ToolRegistry:
        return ToolRegistry(t for t in self._tools.values() if t.kind in READ_ONLY_KINDS)


def builtin_tools() -> list[Tool]:
    return [
        ReadTool(),
        WriteTool(),
        EditTool(),
        GlobTool(),
        GrepTool(),
        LsTool(),
        BashTool(),
        TaskTool(),
        MemoryReadTool(),
        MemoryAppendTool(),
        SessionSearchTool(),
    ]
