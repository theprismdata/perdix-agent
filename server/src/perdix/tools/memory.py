"""메모리 조회·추가 도구. 입력은 실행 경계에서 Pydantic으로 파싱한다."""

from typing import Any

from filelock import Timeout
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from perdix.memory.archive import MemoryArchive
from perdix.memory.schema import MemoryConfig
from perdix.memory.store import MemoryScope, MemoryStore
from perdix.tools.base import ToolContext, ToolKind, ToolOutput, tool_error


class ReadMemoryInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    scope: MemoryScope = "project"


class AppendMemoryInput(ReadMemoryInput):
    content: str = Field(min_length=1, max_length=4000, pattern=r"\S")


class MemoryReadTool:
    name = "memory_read"
    description = "프로젝트 또는 전역의 세션 간 공유 메모리를 읽는다. 기본 범위는 project."
    input_schema = ReadMemoryInput.model_json_schema()
    kind: ToolKind = "read"

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return True

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        try:
            args = ReadMemoryInput.model_validate(input)
            store = MemoryStore(ctx.cwd)
            archive = MemoryArchive(ctx.memory_root or MemoryConfig().root)
            project = str(ctx.cwd.resolve()) if args.scope == "project" else None
            generated = "\n".join(
                f"{entry.key}: {entry.text} (출처: {', '.join(entry.sources)})"
                for entry in archive.snapshot().entries if entry.project_path == project
            )
            return ToolOutput(content=(
                f"{store.path(args.scope)}\n{store.read(args.scope)}\n"
                f"자동 메모리 ({archive.root}):\n{generated}"
            ))
        except (OSError, UnicodeError, ValidationError) as exc:
            return tool_error(str(exc))


class MemoryAppendTool:
    name = "memory_append"
    description = (
        "사용자가 기억을 요청한 사실을 공유 메모리에 추가한다. 기존 기록은 보존한다. "
        "기본 project는 현재 프로젝트에서, global은 모든 프로젝트에서 참조한다."
    )
    input_schema = AppendMemoryInput.model_json_schema()
    kind: ToolKind = "edit"

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        try:
            args = AppendMemoryInput.model_validate(input)
            path = MemoryStore(ctx.cwd).append(args.scope, args.content)
            return ToolOutput(content=f"메모리 추가 완료: {path}")
        except (OSError, UnicodeError, Timeout, ValidationError) as exc:
            return tool_error(str(exc))
