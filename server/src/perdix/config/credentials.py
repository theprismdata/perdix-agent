"""공급자 API 키 로딩.

우선순위: 환경변수(`ANTHROPIC_API_KEY` 등) → `~/.perdix/credentials.json`.
파일 형식은 공급자별 객체다.

    {"anthropic": {"api_key": "sk-ant-..."}, "openai": {"api_key": "sk-..."}}

OAuth 토큰(`auth_token`)은 API 키와 별도 필드다. `ANTHROPIC_AUTH_TOKEN` 환경변수 또는
`{"anthropic": {"auth_token": "..."}}`로 준다. 어댑터는 토큰이 있으면 Bearer 인증을 쓴다.

키는 공급자 클라이언트에만 전달되고 하위 프로세스 환경에는 들어가지 않는다
(sandbox.scrub_env가 `*_API_KEY`, `*_TOKEN`을 제거한다). 파일 권한이 0600보다 느슨하면 경고한다.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

DEFAULT_CREDENTIALS_PATH = Path.home() / ".perdix" / "credentials.json"

ENV_KEYS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

AUTH_TOKEN_ENV_KEYS: dict[str, str] = {
    "anthropic": "ANTHROPIC_AUTH_TOKEN",
}


class ProviderCredential(BaseModel):
    api_key: str | None = None
    auth_token: str | None = None
    base_url: str | None = None


class Credentials(BaseModel):
    providers: dict[str, ProviderCredential] = {}

    def api_key(self, provider: str, env: dict[str, str] | None = None) -> str | None:
        """env가 파일보다 우선한다."""
        environ = os.environ if env is None else env
        env_name = ENV_KEYS.get(provider)
        if env_name and environ.get(env_name):
            return environ[env_name]
        cred = self.providers.get(provider)
        return cred.api_key if cred else None

    def auth_token(self, provider: str, env: dict[str, str] | None = None) -> str | None:
        """OAuth 토큰. env가 파일보다 우선한다."""
        environ = os.environ if env is None else env
        env_name = AUTH_TOKEN_ENV_KEYS.get(provider)
        if env_name and environ.get(env_name):
            return environ[env_name]
        cred = self.providers.get(provider)
        return cred.auth_token if cred else None


def load_credentials(path: Path | None = None) -> Credentials:
    """파일이 없으면 빈 Credentials. 깨진 JSON은 경고 후 빈 값으로 취급한다."""
    target = DEFAULT_CREDENTIALS_PATH if path is None else path
    if not target.is_file():
        return Credentials()
    _warn_if_permissive(target)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("credentials file %s unreadable: %s", target, exc)
        return Credentials()
    if not isinstance(raw, dict):
        logger.warning("credentials file %s must be a JSON object", target)
        return Credentials()
    providers: dict[str, ProviderCredential] = {}
    for name, value in raw.items():
        if isinstance(value, dict):
            providers[name] = ProviderCredential(
                api_key=value.get("api_key"),
                auth_token=value.get("auth_token"),
                base_url=value.get("base_url"),
            )
        elif isinstance(value, str):  # {"anthropic": "sk-..."} 축약형도 허용
            providers[name] = ProviderCredential(api_key=value)
    return Credentials(providers=providers)


def _warn_if_permissive(path: Path) -> None:
    if os.name != "posix":
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        logger.warning(
            "credentials file %s is readable by others (mode %o); run: chmod 600 %s",
            path, mode, path,
        )
