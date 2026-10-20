"""MCP 도구를 Perdix `Tool` 프로토콜로 감싼다(ADR 0034)."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from perdix.mcp.client import McpCancelled, McpClient, McpError
from perdix.tools.base import ToolContext, ToolKind, ToolOutput, tool_error

MAX_RESULT_CHARS = 100_000
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_CHARS = 4000
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def tool_name(server: str, remote: str, taken: set[str]) -> str:
    """공급자 제약(`[A-Za-z0-9_-]{1,64}`)에 맞는 고유 이름을 만든다."""
    base = f"mcp__{_UNSAFE.sub('_', server)}__{_UNSAFE.sub('_', remote)}"
    name = base[:MAX_NAME_LENGTH]
    if name in taken or len(base) > MAX_NAME_LENGTH:
        digest = hashlib.sha256(f"{server}\0{remote}".encode()).hexdigest()[:8]
        name = f"{base[:MAX_NAME_LENGTH - 9]}_{digest}"
    return name


def _safe_schema(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict) or schema.get("type") not in (None, "object"):
        return {"type": "object"}
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        return {"type": "object"}
    return {**schema, "type": "object"}


def omit_properties(schema: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    """모델에게 보여 줄 스키마에서 선택 인수를 숨긴다. 필수 인수는 숨기지 않는다."""
    if not names or not isinstance(schema.get("properties"), dict):
        return schema
    required = set(schema.get("required") or [])
    hidden = {name for name in names if name not in required}
    properties = {k: v for k, v in schema["properties"].items() if k not in hidden}
    return {**schema, "properties": properties}


def render_result(result: dict[str, Any]) -> str:
    """결과 content를 모델이 읽을 텍스트로 바꾼다. 이미지·오디오는 자리 표시로 대체한다."""
    parts: list[str] = []
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif kind in ("image", "audio"):
            parts.append(f"[{kind} {block.get('mimeType', '')} 생략]")
        elif kind == "resource_link":
            parts.append(f"[resource] {block.get('uri', '')}")
        elif kind == "resource" and isinstance(block.get("resource"), dict):
            resource = block["resource"]
            parts.append(resource["text"] if isinstance(resource.get("text"), str)
                         else f"[resource] {resource.get('uri', '')}")
    text = "\n".join(parts)
    if not text and result.get("structuredContent") is not None:
        import json

        text = json.dumps(result["structuredContent"], ensure_ascii=False)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n…(결과가 길어 {MAX_RESULT_CHARS}자에서 잘랐습니다)"
    return text


class McpTool:
    def __init__(
        self,
        *,
        name: str,
        server: str,
        remote_name: str,
        description: str,
        input_schema: Any,
        client: McpClient,
        read_only: bool,
        hint: str = "",
        omit_params: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.server = server
        self.remote_name = remote_name
        text = f"[MCP {server}] {description}"[:MAX_DESCRIPTION_CHARS]
        self.description = f"{text}\n사용 안내: {hint}" if hint else text
        self.input_schema = omit_properties(_safe_schema(input_schema), omit_params)
        self.kind: ToolKind = "search" if read_only else "other"
        self._client = client
        self._read_only = read_only

    def is_read_only(self, input: dict[str, Any]) -> bool:
        # 서버의 annotations는 신뢰하지 않는다. 사용자가 설정에 명시한 readOnly만 따른다.
        return self._read_only

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        try:
            result = await self._client.call_tool(
                self.remote_name, input, cancel=ctx.cancel
            )
        except McpCancelled:
            return tool_error("취소되었습니다.")
        except McpError as exc:
            return tool_error(str(exc))
        text = render_result(result)
        if result.get("isError") is True:
            return tool_error(text or "MCP 도구가 오류를 반환했습니다.")
        return ToolOutput(content=text or "(빈 결과)")
