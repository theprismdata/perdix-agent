"""역할별 모델 오버라이드 설정 (architecture.md §6.10).

메인 모드(chat/plan/build)와 서브에이전트 프리셋에 적용된다.
파일 형식은 모델 별칭 표(`models`)와 역할→모델 매핑(`agents`)이다.

    {"models": {"cheap": {"provider": "anthropic", "model": "claude-haiku-4-5"}},
     "agents": {"explore": {"model": "cheap"}, "executor": {"model": "claude-opus-5-5",
                                                         "effort": "high"}}}
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from perdix.llm import catalog


class ModelAlias(BaseModel):
    provider: Literal["anthropic", "openai", "gemini", "openrouter"] = "anthropic"
    model: str
    capabilities: catalog.ModelInfo | None = None


class AgentRoleConfig(BaseModel):
    # 별칭 이름 또는 리터럴 모델 id
    model: str
    effort: str | None = None


class AgentsConfig(BaseModel):
    models: dict[str, ModelAlias] = {}
    agents: dict[str, AgentRoleConfig] = {}

    def resolve(self, value: str) -> str:
        """별칭이면 실제 모델 id로, 아니면 그대로(리터럴 모델 id로 간주)."""
        alias = self.models.get(value)
        return alias.model if alias is not None else value

    def role(self, role_id: str) -> AgentRoleConfig | None:
        override = self.agents.get(role_id)
        if override is None:
            return None
        return override.model_copy(update={"model": self.resolve(override.model)})

    def provider_for(
        self, model: str,
    ) -> Literal["anthropic", "openai", "gemini", "openrouter"] | None:
        return next(
            (alias.provider for alias in self.models.values() if alias.model == model), None,
        )

    def model_ids(self) -> set[str]:
        return {self.resolve(role.model) for role in self.agents.values()}


AGENT_ROLES = (
    "chat", "plan", "build", "explore", "executor", "reviewer", "debugger",
    "security-reviewer", "compactor", "titler",
)


def assign_openrouter(path: Path | None, role: str, model: str) -> AgentsConfig:
    if role not in AGENT_ROLES:
        raise ValueError("unknown agent role")
    target = path or Path.home() / ".perdix" / "agents.json"
    selected_info = catalog.get(model)
    config = load_agents_config(path) if target.exists() else AgentsConfig()
    base_alias = f"openrouter_{role.replace('-', '_')}"
    alias = base_alias
    shared = {assigned.model for name, assigned in config.agents.items() if name != role}
    suffix = 2
    while alias in shared:
        alias = f"{base_alias}_{suffix}"
        suffix += 1
    config.models[alias] = ModelAlias(
        provider="openrouter", model=model, capabilities=selected_info,
    )
    previous = config.agents.get(role)
    config.agents[role] = AgentRoleConfig(
        model=alias, effort=previous.effort if previous else None,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(config.model_dump_json(indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        catalog.register(selected_info)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return config


def load_agents_config(path: Path | None) -> AgentsConfig:
    """명시적 경로 또는 사용자 설정 폴더의 agents.json을 읽는다."""
    if path is None:
        path = Path.home() / ".perdix" / "agents.json"
        if not path.is_file():
            return AgentsConfig()
    data = json.loads(path.read_text())
    config = AgentsConfig.model_validate(data)
    for alias in config.models.values():
        if alias.capabilities is not None:
            catalog.register(alias.capabilities.model_copy(update={
                "id": alias.model, "provider": alias.provider,
            }))
    return config
