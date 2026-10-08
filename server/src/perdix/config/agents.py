"""역할별 모델 오버라이드 설정 (architecture.md §6.10).

서브에이전트 프리셋(explore/executor/reviewer/compactor/titler)에만 적용된다.
메인 세션 모델은 `--model`/`--effort`와 `/model`이 정하며 여기서 다루지 않는다.
파일 형식은 모델 별칭 표(`models`)와 역할→모델 매핑(`agents`)이다.

    {"models": {"cheap": {"provider": "anthropic", "model": "claude-haiku-4-5"}},
     "agents": {"explore": {"model": "cheap"}, "executor": {"model": "claude-opus-5-5",
                                                         "effort": "high"}}}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class ModelAlias(BaseModel):
    provider: Literal["anthropic", "openai"] = "anthropic"
    model: str


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


def load_agents_config(path: Path | None) -> AgentsConfig:
    """경로가 없으면 빈 설정(오버라이드 없음)을 돌려준다."""
    if path is None:
        return AgentsConfig()
    data = json.loads(path.read_text())
    return AgentsConfig.model_validate(data)
