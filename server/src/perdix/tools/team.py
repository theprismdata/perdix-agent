from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from perdix.teams.models import TeamCommand, TeamError
from perdix.teams.store import TeamStore
from perdix.tools.base import ToolContext, ToolKind, ToolOutput, tool_error


class TeamTool:
    name = "team"
    description = (
        "팀 상태 조회와 에이전트 간 통신. action=status/send/broadcast/assign/complete/block/"
        "remove/stop. assign에는 task_id, recipient, body와 수정할 상대 paths를 지정한다. "
        "배정·재배정·제거·종료는 리더만 가능하다. 작업 완료 또는 실패 시 complete/block으로 "
        "결과를 기록한다. send로 질문과 결과를 전달한다. 메시지는 상대의 다음 실행에 전달된다. "
        "담당 파일 범위를 지키며 다른 구성원의 변경을 되돌리지 않는다."
    )
    input_schema: dict[str, Any] = TeamCommand.model_json_schema()
    kind: ToolKind = "think"

    def __init__(self, store: TeamStore) -> None:
        self.store = store

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        try:
            command = TeamCommand.model_validate(input)
            return ToolOutput(content=await self.store.command(command))
        except (TeamError, ValidationError) as exc:
            return tool_error(str(exc))
