"""이름 있는 인증 프로필. 전역 기본 선택과 비밀 파일을 별도로 저장한다."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from perdix.config.credentials import DEFAULT_CREDENTIALS_PATH, load_credentials
from perdix.llm.openai_credentials import oauth_path, read_record

AuthProvider = Literal["anthropic", "openai", "gemini", "openrouter"]
AuthMethod = Literal["api_key", "oauth"]
ProfileName = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")]
PROVIDERS: tuple[AuthProvider, ...] = ("anthropic", "openai", "gemini", "openrouter")
LEGACY = "legacy"


class AuthProfileError(ValueError):
    """사용자에게 비밀 없이 전달할 수 있는 프로필 오류."""


class AuthProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: ProfileName
    provider: AuthProvider
    method: AuthMethod
    enabled: bool = True

    @model_validator(mode="after")
    def check_method(self) -> AuthProfile:
        if self.name == LEGACY:
            raise AuthProfileError("legacy는 기존 인증용 예약 이름입니다")
        if self.method == "oauth" and self.provider not in ("anthropic", "openai"):
            raise AuthProfileError("이 제공자는 API 키 인증만 지원합니다")
        return self


class ProfileRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profiles: dict[str, AuthProfile] = Field(default_factory=dict)
    defaults: dict[AuthProvider, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_references(self) -> ProfileRegistry:
        if any(name != profile.name for name, profile in self.profiles.items()):
            raise AuthProfileError("프로필 이름과 저장 키가 다릅니다")
        for provider, name in self.defaults.items():
            self.get_profile(provider, name)
        return self

    def get_profile(self, provider: str, name: str) -> AuthProfile | None:
        if name == LEGACY:
            return None
        profile = self.profiles.get(name)
        if profile is None or profile.provider != provider:
            raise AuthProfileError(f"{provider} 인증 프로필을 찾을 수 없습니다: {name}")
        return profile


def private_write(path: Path, content: str) -> None:
    """기존 파일 권한과 관계없이 비밀을 0600 파일로 원자 교체한다."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".auth-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class AuthProfiles:
    def __init__(self, credentials_path: Path = DEFAULT_CREDENTIALS_PATH) -> None:
        self.credentials_path = credentials_path
        self.path = credentials_path.with_name("auth-profiles.json")

    def read(self) -> ProfileRegistry:
        if not self.path.exists():
            return ProfileRegistry()
        # 손상된 설정을 빈 설정으로 대체하면 다른 계정으로 호출할 수 있으므로 실패한다.
        try:
            return ProfileRegistry.model_validate_json(self.path.read_bytes())
        except ValidationError as exc:
            raise AuthProfileError("auth-profiles.json 형식을 확인하세요") from exc

    def credential_path(self, profile: AuthProfile) -> Path:
        return self.path.parent / "auth-profiles" / profile.name / "credentials.json"

    def save(self, profile: AuthProfile) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(self.path) + ".lock", mode=0o600):
            registry = self.read()
            previous = registry.profiles.get(profile.name)
            if previous and (previous.provider, previous.method) != (
                profile.provider, profile.method,
            ):
                raise AuthProfileError("기존 프로필의 제공자·인증 방식은 바꿀 수 없습니다")
            registry.profiles[profile.name] = profile
            private_write(self.path, registry.model_dump_json(indent=2) + "\n")

    def use(self, provider: AuthProvider, name: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(self.path) + ".lock", mode=0o600):
            registry = self.read()
            registry.get_profile(provider, name)
            registry.defaults[provider] = name
            private_write(self.path, registry.model_dump_json(indent=2) + "\n")

    def defaults(self) -> dict[str, str]:
        registry = self.read()
        return {provider: registry.defaults.get(provider, LEGACY) for provider in PROVIDERS}

    def ready(self, profile: AuthProfile) -> bool:
        try:
            return self._ready(profile)
        except (ValidationError, OSError):
            return False

    def _ready(self, profile: AuthProfile) -> bool:
        if not profile.enabled:
            return False
        path = self.credential_path(profile)
        match profile.method:
            case "api_key":
                return bool(load_credentials(path).api_key(profile.provider, env={}))
            case "oauth":
                if profile.provider == "anthropic":
                    return True  # 실제 만료·갱신 상태는 ant SDK가 판단한다.
                record = read_record(oauth_path(path))
                return bool(record and record.refresh_token)

    def describe(self, provider: str, name: str) -> str:
        profile = self.read().get_profile(provider, name)
        if profile is None:
            return f"{provider}: legacy · 기존 환경변수/키/로그인"
        state = "저장됨" if self.ready(profile) else "재인증 필요"
        method = "API 키" if profile.method == "api_key" else "계정 로그인"
        return f"{provider}: {name} · {method} · {state}"
