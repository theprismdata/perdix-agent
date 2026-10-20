from collections.abc import Awaitable, Callable
from typing import Any

from perdix.tools.base import ToolContext, ToolKind, ToolOutput


class SwitchModeTool:
    name = "switch_mode"
    description = (
        "요청에 필요한 세션 모드로 전환한다. 질문·설명·조사는 general, "
        "계획만 요청하면 plan, 구현·수정·실행은 build. "
        "현재 모드와 다르면 작업 전에 이 도구만 단독 호출하고 결과를 기다린다. "
        "모드 전환은 파일 수정·실행 권한 승인을 우회하지 않는다."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"mode": {"type": "string", "enum": ["general", "plan", "build"]}},
        "required": ["mode"],
        "additionalProperties": False,
    }
    kind: ToolKind = "think"

    def __init__(self, switch: Callable[[str], Awaitable[None]]) -> None:
        self._switch = switch

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        await self._switch(input["mode"])
        return ToolOutput(content=f"현재 모드: {input['mode']}. 갱신된 도구로 작업을 계속하세요.")
