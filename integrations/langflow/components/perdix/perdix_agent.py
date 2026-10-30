"""Perdix 에이전트 구성(시스템 프롬프트·모델·도구)을 Langflow 노드로 내놓는 컴포넌트 (ADR 0027).

Perdix 중계 서버(`perdix-relay`)의 HTTP 계약만 쓴다. Langflow 내부나 Perdix 내부를 import하지 않는다.
- `GET  {url}/v1/agents/{role}`  역할 정의(시스템 프롬프트·모델·도구 목록)
- `POST {url}/v1/chat/completions` 모델 `perdix:role/{role}` (OpenAI 호환, Perdix 인증으로 호출)
- `POST {url}/mcp/{role}`        역할이 받는 내장 도구(MCP JSON-RPC)
세 출력을 표준 Agent 노드의 Instructions / Language Model(Connect other models) / Tools에 연결한다.
Perdix에서 역할을 바꾸면 이 노드를 다시 실행할 때 그대로 따라온다.
"""

from __future__ import annotations

from typing import Any

import httpx
from langchain_core.tools import StructuredTool, ToolException
from lfx.custom.custom_component.component import Component
from lfx.io import DropdownInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

ROLES = [
    "chat", "plan", "build", "explore", "executor", "reviewer", "debugger",
    "security-reviewer", "compactor", "titler",
]
TIMEOUT = 120.0


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter() if getter else value or "")


class PerdixAgentComponent(Component):
    display_name = "Perdix Agent"
    description = "Perdix 에이전트 역할의 시스템 프롬프트, 모델, 도구를 그대로 가져옵니다."
    icon = "bot"
    name = "PerdixAgent"

    inputs = [
        DropdownInput(
            name="role", display_name="Role", options=ROLES, value="explore",
            info="Perdix 에이전트 역할. 프롬프트·모델·도구가 Perdix와 같습니다.",
            combobox=True,
        ),
        StrInput(
            name="relay_url", display_name="Perdix Relay URL", value="http://127.0.0.1:18796",
            info="perdix-relay 주소", advanced=True,
        ),
        SecretStrInput(
            name="relay_token", display_name="Relay Token", value="PERDIX_RELAY_TOKEN",
            info="perdix-relay Bearer 토큰(Langflow 변수 이름 또는 값)", advanced=True,
        ),
    ]
    outputs = [
        # group_outputs: 세 출력을 한 노드에서 동시에 연결한다(기본은 하나만 선택해 연결).
        Output(display_name="Instructions", name="instructions", method="get_instructions",
               types=["Message"], group_outputs=True),
        Output(display_name="Language Model", name="model", method="get_model",
               types=["LanguageModel"], group_outputs=True),
        Output(display_name="Tools", name="tools", method="get_tools", types=["Tool"],
               group_outputs=True),
    ]

    # ------------------------------------------------------------ 공통

    def _base(self) -> str:
        return str(self.relay_url).rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {_secret(self.relay_token)}"}

    def _definition(self) -> dict[str, Any]:
        response = httpx.get(f"{self._base()}/v1/agents/{self.role}", headers=self._headers(),
                             timeout=TIMEOUT)
        if response.status_code >= 400:
            msg = f"Perdix 역할 정의를 가져오지 못했습니다({response.status_code}): {response.text[:200]}"
            raise ValueError(msg)
        return response.json()

    # ------------------------------------------------------------ 출력

    def get_instructions(self) -> Message:
        definition = self._definition()
        self.status = f"{definition['role']} / {definition['model']}"
        return Message(text=definition["systemPrompt"])

    def get_model(self) -> Any:
        from langchain_openai import ChatOpenAI

        # `perdix:` 접두사는 langchain이 gpt-5 이상 이름을 Responses API로 보내는 것을 피한다.
        return ChatOpenAI(
            model=f"perdix:role/{self.role}", base_url=f"{self._base()}/v1",
            api_key=_secret(self.relay_token), streaming=True, max_retries=2, timeout=TIMEOUT,
        )

    def get_tools(self) -> list[StructuredTool]:
        definition = self._definition()
        return [self._tool(spec) for spec in definition["tools"]]

    # ------------------------------------------------------------ 도구

    def _rpc(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}

    @staticmethod
    def _text(reply: dict[str, Any]) -> str:
        if "error" in reply:
            raise ToolException(str(reply["error"].get("message", reply["error"])))
        result = reply["result"]
        text = "\n".join(c.get("text", "") for c in result.get("content", []))
        if result.get("isError"):
            raise ToolException(text)
        return text

    def _tool(self, spec: dict[str, Any]) -> StructuredTool:
        url = f"{self._base()}/mcp/{self.role}"
        headers = self._headers()
        name = spec["name"]

        def run(**arguments: Any) -> str:
            reply = httpx.post(url, json=self._rpc(name, arguments), headers=headers,
                               timeout=TIMEOUT)
            return self._text(reply.json())

        async def arun(**arguments: Any) -> str:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                reply = await client.post(url, json=self._rpc(name, arguments), headers=headers)
            return self._text(reply.json())

        return StructuredTool(
            name=name, description=spec["description"], args_schema=spec["inputSchema"],
            func=run, coroutine=arun, handle_tool_error=True,
        )
