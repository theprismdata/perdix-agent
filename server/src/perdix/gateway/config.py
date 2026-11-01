import hashlib
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from perdix.config.schema import ServerConfig


class AcpAgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    command: tuple[str, ...] = Field(min_length=1)
    mode: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def check_command(self) -> "AcpAgentConfig":
        if any(not part.strip() or "\x00" in part for part in self.command):
            raise ValueError("ACP 명령은 비어 있지 않은 실행 파일과 인수 배열이어야 합니다")
        return self

    @property
    def identity(self) -> str:
        return "acp:" + hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class OntologyConfig(BaseModel):
    """이 프로젝트의 온톨로지 드라이런에 쓸 MCP 서버와 문서 경로.

    `mcp_server`는 이 프로젝트의 cwd에 대해 `perdix.mcp.config.resolve_servers`가
    돌려주는 이름 중 하나여야 한다(신뢰 경계는 그 함수가 이미 처리한다).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    mcp_server: str = Field(min_length=1)
    doc_paths: tuple[Path, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def check_doc_paths(self) -> "OntologyConfig":
        if any(not path.is_absolute() for path in self.doc_paths):
            raise ValueError("doc_paths 항목은 절대 경로여야 합니다")
        return self


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    cwd: Path
    trust_project: bool = False
    github_repository: str | None = None
    acp: AcpAgentConfig | None = None
    ontology: OntologyConfig | None = None

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


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    host: str = "127.0.0.1"
    port: int = Field(default=18790, ge=1, le=65535)
    token_env: str = "PERDIX_GATEWAY_TOKEN"
    # "none"은 이 컴퓨터에서만 접속하는 대시보드용이다. 루프백 호스트에서만 허용한다.
    # 지정하지 않으면 개인용 기본값으로 루프백은 none, 그 밖의 호스트는 token이다.
    auth: Literal["token", "none"] = "token"
    db_path: Path = Path.home() / ".perdix" / "gateway.db"
    projects: dict[str, ProjectConfig] = Field(min_length=1)
    server: ServerConfig = Field(default_factory=ServerConfig)
    max_concurrent_jobs: int = Field(default=2, ge=1, le=8)
    telegram: TelegramConfig | None = None
    github: GitHubConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def default_auth(cls, data: Any) -> Any:
        if isinstance(data, dict) and "auth" not in data:
            loopback = data.get("host", "127.0.0.1") in LOOPBACK_HOSTS
            return {**data, "auth": "none" if loopback else "token"}
        return data

    @model_validator(mode="after")
    def check_projects(self) -> "GatewayConfig":
        if self.auth == "none" and self.host not in LOOPBACK_HOSTS:
            raise ValueError("auth가 none이면 host는 127.0.0.1, localhost, ::1 중 하나여야 합니다")
        for name in self.projects:
            if not name or len(name) > 100 or any(character.isspace() for character in name):
                raise ValueError("프로젝트 이름은 공백 없는 1~100자여야 합니다")
        if self.server.trust_project:
            raise ValueError("프로젝트 신뢰는 projects 항목의 trust_project로 설정하세요")
        return self


class GatewaySecrets(BaseModel):
    model_config = ConfigDict(frozen=True)
    gateway: SecretStr | None = None
    telegram: SecretStr | None = None
    github: SecretStr | None = None
    webhook: SecretStr | None = None

    @field_validator("gateway")
    @classmethod
    def check_gateway_length(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 24:
            raise ValueError("연결 토큰은 24자 이상이어야 합니다")
        return value
