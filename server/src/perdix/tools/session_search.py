"""저장된 세션의 FTS5 교차 검색 도구."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from perdix.tools.base import ToolContext, ToolKind, ToolOutput, tool_error


class SessionSearchInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    query: str = Field(min_length=1, max_length=1000)
    scope: Literal["all", "project"] = "all"
    limit: int = Field(default=20, ge=1, le=50)


class SessionSearchTool:
    name = "session_search"
    description = (
        "과거 세션 대화를 검색한다. 기본 all은 모든 프로젝트, project는 현재 프로젝트. "
        "같은 프로젝트 결과를 우선하며 세션 ID·프로젝트 경로·시각·텍스트를 반환한다."
    )
    input_schema = SessionSearchInput.model_json_schema()
    kind: ToolKind = "search"

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return True

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        if ctx.session_store is None:
            return tool_error("이 실행 컨텍스트에는 세션 저장소가 없습니다")
        try:
            args = SessionSearchInput.model_validate(input)
        except ValidationError as exc:
            return tool_error(str(exc))
        hits = await ctx.session_store.search(
            args.query, args.scope, str(ctx.cwd), limit=args.limit
        )
        return ToolOutput(content=json.dumps(
            [hit.model_dump() for hit in hits], ensure_ascii=False
        ))
