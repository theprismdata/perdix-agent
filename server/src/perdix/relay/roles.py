"""Perdix 역할(에이전트) 정의: 시스템 프롬프트·모델·도구 (ADR 0027).

Perdix 안에서 역할이 실제로 쓰는 것과 같은 값을 외부 도구(Langflow)에 내보낸다.
- 시스템 프롬프트: `build_system_prompt(cwd, mode)` (프로젝트 지침·메모리 포함)
- 모델: `agents.json` 역할 할당 → 프리셋 기본값 → 기본 모델
- 도구: 모드/프리셋의 읽기 전용 여부로 레지스트리를 좁힌 내장 도구
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from perdix.config.agents import AGENT_ROLES, AgentsConfig
from perdix.config.schema import DEFAULT_MODEL
from perdix.context.builder import build_system_prompt
from perdix.harness.modes import MODES, Mode
from perdix.memory.schema import MemoryConfig
from perdix.subagents.presets import PRESETS
from perdix.subagents.presets import get as get_preset
from perdix.tools.base import Tool
from perdix.tools.registry import ToolRegistry, builtin_tools

# 실행에 세션 저장소·서브에이전트·메모리 폴더가 필요해 중계 서버가 제공하지 못하는 도구
OMITTED: dict[str, str] = {
    "task": "서브에이전트 실행에 Perdix 세션이 필요하다",
    "session_search": "Perdix 세션 저장소가 필요하다",
    "memory_read": "Perdix 메모리 폴더 접근이 필요하다",
    "memory_append": "Perdix 메모리 폴더 접근이 필요하다",
}


class RoleDefinition(BaseModel):
    role: str
    description: str
    model: str
    effort: str | None
    readOnly: bool  # noqa: N815 - 와이어 키는 camelCase(AGENTS.md 규칙 11)
    systemPrompt: str  # noqa: N815
    tools: list[dict[str, Any]]
    omittedTools: dict[str, str]  # noqa: N815


def role_mode(role: str) -> Mode:
    if role in MODES:
        return MODES[role]
    return PRESETS[role].mode  # type: ignore[index]


class Roles:
    def __init__(
        self, workspace: Path, agents_config: AgentsConfig, memories: MemoryConfig | None = None
    ) -> None:
        self.workspace = workspace.resolve()
        self.agents_config = agents_config
        self.memories = memories or MemoryConfig()
        self._tools: dict[str, Tool] = {
            t.name: t for t in builtin_tools() if t.name not in OMITTED
        }

    def names(self) -> list[str]:
        return list(AGENT_ROLES)

    def known(self, role: str) -> bool:
        return role in AGENT_ROLES

    def model_and_effort(self, role: str) -> tuple[str, str | None]:
        if role in PRESETS:
            preset = get_preset(role, self.agents_config)
            return preset.model, preset.effort
        assigned = self.agents_config.role(role)
        if assigned is not None:
            return assigned.model, assigned.effort
        return DEFAULT_MODEL, None

    def tools_for(self, role: str) -> dict[str, Tool]:
        """모드의 읽기 전용 여부로 좁힌 도구. TurnRunner 레지스트리와 같은 규칙이다."""
        registry = ToolRegistry(self._tools.values())
        if role_mode(role).read_only:
            registry = registry.read_only_only()
        return {name: registry.get(name) for name in registry.names()}  # type: ignore[misc]

    def definition(self, role: str) -> RoleDefinition:
        mode = role_mode(role)
        model, effort = self.model_and_effort(role)
        return RoleDefinition(
            role=role, description=mode.description, model=model, effort=effort,
            readOnly=mode.read_only,
            systemPrompt=build_system_prompt(self.workspace, mode, self.memories),
            tools=[{"name": t.name, "description": t.description, "inputSchema": t.input_schema,
                    "kind": t.kind} for t in self.tools_for(role).values()],
            omittedTools=dict(OMITTED),
        )
