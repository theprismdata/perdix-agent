"""MCP 연결 허브. 세션 사이에서 서버 프로세스를 재사용한다(ADR 0034)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from perdix.mcp.client import McpClient, McpError
from perdix.mcp.config import (
    DEFAULT_GLOBAL_PATH,
    McpServerConfig,
    resolve_servers,
)
from perdix.mcp.tool import McpTool, tool_name
from perdix.tools.base import Tool

logger = logging.getLogger(__name__)


@dataclass
class McpSessionTools:
    tools: list[Tool] = field(default_factory=list)
    # 사용자가 readOnly로 지정한 서버의 도구 이름. 권한 정책에서 자동 허용한다.
    auto_allow: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


def _fingerprint(name: str, config: McpServerConfig, cwd: Path) -> str:
    return json.dumps(
        [name, config.command, config.args, sorted(config.env.items()), config.cwd or str(cwd)]
    )


class McpHub:
    def __init__(self, global_path: Path = DEFAULT_GLOBAL_PATH) -> None:
        self._global_path = global_path
        self._clients: dict[str, McpClient] = {}
        self._lock = asyncio.Lock()

    async def tools_for(
        self,
        cwd: Path,
        *,
        trust_project: bool,
        client_servers: Mapping[str, McpServerConfig] | None = None,
    ) -> McpSessionTools:
        servers, warnings = resolve_servers(
            cwd, trust_project=trust_project, client=client_servers,
            global_path=self._global_path,
        )
        result = McpSessionTools(warnings=list(warnings))
        for warning in warnings:
            logger.warning("MCP 설정: %s", warning)
        if not servers:
            return result
        loaded = await asyncio.gather(
            *(self._load(name, config, cwd) for name, config in servers.items())
        )
        taken: set[str] = set()
        for (name, config), (tools, error) in zip(servers.items(), loaded, strict=True):
            if error:
                result.warnings.append(error)
                logger.warning("MCP %s", error)
                continue
            for remote in tools:
                remote_name = remote["name"]
                local = tool_name(name, remote_name, taken)
                taken.add(local)
                client = self._clients[_fingerprint(name, config, cwd)]
                result.tools.append(McpTool(
                    name=local, server=name, remote_name=remote_name,
                    description=str(remote.get("description") or remote.get("title") or ""),
                    input_schema=remote.get("inputSchema"), client=client,
                    read_only=config.read_only, hint=config.hint,
                    omit_params=config.omit_params.get(remote_name, ()),
                ))
                if config.read_only:
                    result.auto_allow.add(local)
        return result

    async def _load(
        self, name: str, config: McpServerConfig, cwd: Path
    ) -> tuple[list[dict[str, Any]], str | None]:
        key = _fingerprint(name, config, cwd)
        try:
            async with self._lock:
                client = self._clients.get(key)
                if client is None or not client.alive:
                    if client is not None:
                        await client.close()
                    client = McpClient(name, config, cwd)
                    await client.start()
                    self._clients[key] = client
            return await client.list_tools(), None
        except McpError as exc:
            self._clients.pop(key, None)
            return [], str(exc)
        except Exception as exc:
            logger.exception("MCP %s 연결 중 예기치 않은 오류", name)
            self._clients.pop(key, None)
            return [], f"{name}: 예기치 않은 오류 ({type(exc).__name__})"

    async def aclose(self) -> None:
        clients, self._clients = list(self._clients.values()), {}
        await asyncio.gather(*(c.close() for c in clients), return_exceptions=True)
