"""Perdix 내장 도구를 역할별 MCP(streamable HTTP, JSON 응답)로 노출한다(ADR 0027).

외부 에이전트(Langflow)가 같은 도구·경로 제한·출력 형식을 쓰게 한다. 도구 목록은 역할이
Perdix 안에서 받는 것과 같다. 실행 판정은 Perdix `PermissionPolicy`가 하고, 승인 채널이 없으므로
`ask`는 쓰지 않고 allow가 아니면 거부한다(기본 `deny`: 내장 읽기 전용 도구만 통과).
작업 디렉터리는 기동 시 고정한다.
"""

from __future__ import annotations

from typing import Any

from perdix.permissions.policy import PermissionMode, PermissionPolicy
from perdix.relay.roles import Roles
from perdix.sandbox.local import LocalBackend
from perdix.tools.base import ToolContext, validate_input

SERVER_NAME = "perdix-tools"
RELAY_MODES: tuple[PermissionMode, ...] = ("deny", "accept-edits", "allowlist")


class ToolsMcp:
    def __init__(self, roles: Roles, permission_mode: PermissionMode = "deny") -> None:
        if permission_mode not in RELAY_MODES:
            raise ValueError(f"relay permission mode must be one of {RELAY_MODES}")
        self.roles = roles
        self.policy = PermissionPolicy(mode=permission_mode)
        self._sandbox = LocalBackend()

    async def handle(self, role: str, message: dict[str, Any]) -> dict[str, Any] | None:
        """JSON-RPC 요청 하나를 처리한다. 알림(id 없음)이면 None."""
        method = message.get("method")
        ident = message.get("id")
        if ident is None:
            return None
        params = message.get("params") or {}
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": params.get("protocolVersion") or "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": "0"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [
                {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                for t in self.roles.tools_for(role).values()
            ]}
        elif method == "tools/call":
            result = await self._call(role, str(params.get("name", "")),
                                      params.get("arguments") or {})
        else:
            return {"jsonrpc": "2.0", "id": ident,
                    "error": {"code": -32601, "message": f"method not found: {method}"}}
        return {"jsonrpc": "2.0", "id": ident, "result": result}

    async def _call(self, role: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        def failure(text: str) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": text}], "isError": True}

        tool = self.roles.tools_for(role).get(name)
        if tool is None:
            return failure(f"unknown tool: {name}")
        problems = validate_input(tool, arguments)  # 규칙 5: 검증 실패는 is_error 결과
        if problems:
            return failure("; ".join(problems))
        if self.policy.evaluate(name, arguments) != "allow":
            return failure(
                f"denied by Perdix permission policy (relay mode: {self.policy.mode}). "
                "서버를 --permission-mode accept-edits 등으로 띄워야 쓸 수 있다."
            )
        output = await tool.run(ToolContext(cwd=self.roles.workspace, sandbox=self._sandbox),
                                arguments)
        return {"content": [{"type": "text", "text": output.content}], "isError": output.is_error}
