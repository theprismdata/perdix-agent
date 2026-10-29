"""허용 목록의 Langflow 플로우를 실행하는 선택 도구."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from perdix.integrations.langflow.client import LangflowClient
from perdix.integrations.langflow.config import LangflowConfig
from perdix.integrations.langflow.errors import LangflowError
from perdix.tools.base import ToolContext, ToolKind, ToolOutput, tool_error


class LangflowRunInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    flow: str = Field(min_length=1, max_length=64)
    input: str = Field(min_length=1, max_length=200_000, pattern=r"\S")
    session_id: str | None = Field(default=None, min_length=1, max_length=128)


class LangflowRunTool:
    name = "langflow_run"
    kind: ToolKind = "execute"

    def __init__(self, config: LangflowConfig) -> None:
        flows = "; ".join(
            f"{alias}: {config.flow_descriptions.get(alias, '설명 없음')}"
            for alias in config.flows
        )
        self.description = (
            "미리 허용한 Langflow 플로우에 텍스트를 입력해 실행하고 결과 텍스트를 받는다. "
            "플로우는 외부 서비스를 호출하거나 상태를 바꿀 수 있다. "
            f"사용할 수 있는 flow: {flows}. 같은 session_id를 주면 대화 맥락이 이어진다."
        )
        self.input_schema = {
            **LangflowRunInput.model_json_schema(),
            "properties": {
                **LangflowRunInput.model_json_schema()["properties"],
                "flow": {"type": "string", "enum": list(config.flows), "title": "Flow"},
            },
        }

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        client: LangflowClient | None = ctx.langflow
        if client is None:
            return tool_error("Langflow 연동이 꺼져 있습니다 (--langflow-config로 켭니다)")
        try:
            args = LangflowRunInput.model_validate(input)
            result = await client.run(args.flow, args.input, args.session_id)
        except ValidationError as exc:
            return tool_error(str(exc))
        except LangflowError as exc:
            hint = " 잠시 후 다시 시도할 수 있습니다." if exc.retryable else ""
            return tool_error(f"{exc}{hint}")
        suffix = "\n[출력이 길어 잘렸습니다]" if result.truncated else ""
        return ToolOutput(content=(result.text or "(빈 응답)") + suffix)
