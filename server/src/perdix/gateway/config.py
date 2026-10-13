import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from perdix.config.schema import ServerConfig


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cwd: Path
    trust_project: bool = False
    github_repository: str | None = None

    @model_validator(mode="after")
    def check_directory(self) -> "ProjectConfig":
        if not self.cwd.is_absolute() or not self.cwd.is_dir():
            raise ValueError("cwd는 존재하는 절대 디렉터리여야 합니다")
        if self.github_repository and not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
            self.github_repository,
        ):
            raise ValueError("github_repository는 owner/repo 형식이어야 합니다")
        return self


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    token_env: str = "PERDIX_TELEGRAM_TOKEN"
    allowed_user_ids: list[int] = Field(min_length=1)
    allowed_chat_ids: list[int] = Field(min_length=1)


class GitHubConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    token_env: str = "PERDIX_GITHUB_TOKEN"
    webhook_secret_env: str = "PERDIX_GITHUB_WEBHOOK_SECRET"
    allowed_senders: list[str] = Field(default_factory=list)
    post_results: bool = False


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    host: str = "127.0.0.1"
    port: int = Field(default=18790, ge=1, le=65535)
    token_env: str = "PERDIX_GATEWAY_TOKEN"
    db_path: Path = Path.home() / ".perdix" / "gateway.db"
    projects: dict[str, ProjectConfig] = Field(min_length=1)
    server: ServerConfig = Field(default_factory=ServerConfig)
    max_concurrent_jobs: int = Field(default=2, ge=1, le=8)
    telegram: TelegramConfig | None = None
    github: GitHubConfig | None = None

    @model_validator(mode="after")
    def check_projects(self) -> "GatewayConfig":
        for name in self.projects:
            if not name or len(name) > 100 or any(character.isspace() for character in name):
                raise ValueError("프로젝트 이름은 공백 없는 1~100자여야 합니다")
        if self.server.trust_project:
            raise ValueError("프로젝트 신뢰는 projects 항목의 trust_project로 설정하세요")
        return self


class GatewaySecrets(BaseModel):
    model_config = ConfigDict(frozen=True)
    gateway: SecretStr = Field(min_length=24)
    telegram: SecretStr | None = None
    github: SecretStr | None = None
    webhook: SecretStr | None = None
