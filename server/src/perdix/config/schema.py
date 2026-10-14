"""서버 실행 설정 (argv → ServerConfig). 내부 키는 snake_case(AGENTS.md 규칙 11)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from perdix.config.credentials import DEFAULT_CREDENTIALS_PATH
from perdix.memory.schema import MemoryConfig
from perdix.permissions.policy import PermissionMode
from perdix.session.store import DEFAULT_DB_PATH

ProviderName = Literal["anthropic", "openai", "gemini", "openrouter", "fake"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

DEFAULT_MODEL = "claude-opus-5-5"
# 메인 에이전트 기본 effort. 카탈로그에서 effort를 지원하지 않는 모델이면 보내지 않는다.
DEFAULT_EFFORT = "xhigh"
DEFAULT_MAX_TOKENS = 32_000
# --provider만 주고 --model을 안 주면 쓰는 공급자별 기본 모델.
DEFAULT_MODEL_FOR_PROVIDER: dict[str, str] = {
    "anthropic": DEFAULT_MODEL,
    "openai": "gpt-6.1-sol",
    "gemini": "gemini-3.8-flash",
    "openrouter": "qwen/qwen3-coder-flash",
    "fake": DEFAULT_MODEL,
}


class ServerConfig(BaseModel):
    team: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    member: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    team_role: Literal["leader", "member"] = "member"
    team_worker: bool = False
    team_db: Path = Path.home() / ".perdix" / "teams.sqlite3"
    team_project: Path | None = None
    memories: MemoryConfig = Field(default_factory=MemoryConfig)
    trust_project: bool = False
    db_path: str = str(DEFAULT_DB_PATH)
    provider: ProviderName = "anthropic"
    model: str = DEFAULT_MODEL
    effort: str | None = DEFAULT_EFFORT
    permission_mode: PermissionMode = "interactive"
    max_tokens: int = Field(default=DEFAULT_MAX_TOKENS, gt=0)
    # provider == "fake"일 때 FakeProvider 스크립트(JSON) 경로
    fake_script: Path | None = None
    # 서브에이전트 역할별 모델 오버라이드(JSON) 경로 (config/agents.py)
    agents_config_path: Path | None = None
    log_level: LogLevel = "INFO"
    # 공급자 키 파일. env가 우선한다(config/credentials.py).
    credentials_path: Path = DEFAULT_CREDENTIALS_PATH

    @model_validator(mode="before")
    @classmethod
    def _default_model_for_provider(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("model") is None:
            provider = data.get("provider")
            if isinstance(provider, str) and provider in DEFAULT_MODEL_FOR_PROVIDER:
                data = {**data, "model": DEFAULT_MODEL_FOR_PROVIDER[provider]}
        return data

    @model_validator(mode="after")
    def _check_fake_script(self) -> ServerConfig:
        if (self.team is None) != (self.member is None):
            raise ValueError("--team과 --member를 함께 지정하세요")
        if self.team_worker and self.team is None:
            raise ValueError("--team-worker에는 --team과 --member가 필요합니다")
        if self.provider == "fake" and self.fake_script is None:
            raise ValueError("--provider fake requires --fake-script PATH")
        return self
