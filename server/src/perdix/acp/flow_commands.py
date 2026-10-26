"""`/flows`·`/flow` 명령: 사용자가 직접 허용 목록의 Langflow 플로우를 실행한다.

사용자가 직접 입력한 명령이므로 모델을 거치지 않고 승인 질문도 하지 않지만, 호출 가능한 플로우는
설정의 허용 목록으로 제한되고 모든 오류는 정규화된 메시지로 돌려준다.
"""

from perdix.integrations.langflow.client import LangflowClient
from perdix.integrations.langflow.config import LangflowConfig
from perdix.integrations.langflow.errors import LangflowError

FLOW_COMMANDS = frozenset({"/flows", "/flow"})
DISABLED = "Langflow 연동이 꺼져 있습니다 (--langflow-config로 켭니다)\n"


def flow_list(config: LangflowConfig) -> str:
    lines = ["사용할 수 있는 Langflow 플로우:"]
    for alias in config.flows:
        description = config.flow_descriptions.get(alias, "설명 없음")
        lines.append(f"  {alias}  {description}")
    lines.append("실행: /flow <별칭> <입력>")
    return "\n".join(lines) + "\n"


async def run_flow_command(
    text: str, config: LangflowConfig, client: LangflowClient | None,
    session_id: str | None = None,
) -> str:
    """`/flows` 또는 `/flow <별칭> <입력>`의 응답 텍스트. 모델 호출 없이 끝난다."""
    if client is None or not config.enabled:
        return DISABLED
    words = text.strip().split(maxsplit=2)
    if words[0] == "/flows" or len(words) < 3:
        return flow_list(config)
    alias, user_input = words[1], words[2]
    try:
        result = await client.run(alias, user_input, session_id)
    except LangflowError as exc:
        hint = " 잠시 후 다시 시도할 수 있습니다." if exc.retryable else ""
        return f"{exc}{hint}\n"
    suffix = "\n[출력이 길어 잘렸습니다]" if result.truncated else ""
    return (result.text or "(빈 응답)") + suffix + "\n"
