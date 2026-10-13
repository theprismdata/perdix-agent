from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from perdix.config.credentials import DEFAULT_CREDENTIALS_PATH
from perdix.llm.base import ProviderError


class OAuthRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    client_id: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    ext_agent_host_id: str = Field(min_length=1)
    access_token: SecretStr | None = None
    refresh_token: SecretStr | None = None
    id_token: SecretStr | None = None
    scopes: tuple[str, ...] = ()
    expires_at: float = 0
    earliest_refresh_at: float = 0


class TokenResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr = Field(min_length=1)
    id_token: SecretStr | None = None
    token_type: Literal["Bearer"]
    expires_in: int = Field(gt=0)
    scope: str
    earliest_refresh_at: float = 0


def oauth_path(credentials_path: Path | None = None) -> Path:
    return (credentials_path or DEFAULT_CREDENTIALS_PATH).with_name("openai-oauth.json")


def read_record(path: Path) -> OAuthRecord | None:
    try:
        return OAuthRecord.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValidationError):
        raise ProviderError("ChatGPT 로그인 기록을 읽을 수 없습니다.", retryable=False) from None


def write_record(path: Path, record: OAuthRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = record.model_dump(mode="json")
    for key in ("access_token", "refresh_token", "id_token"):
        token = getattr(record, key)
        data[key] = token.get_secret_value() if token is not None else None
    fd, name = tempfile.mkstemp(prefix=".openai-oauth-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            target.write(json.dumps(data))
            target.flush()
            os.fsync(target.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@asynccontextmanager
async def credential_lock(path: Path) -> AsyncIterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = FileLock(
        str(path) + ".lock", mode=0o600, fallback_to_soft=False, preserve_lock_file=True,
    )
    try:
        async with asyncio.timeout(310):
            while True:
                try:
                    lock.acquire(timeout=0)
                    break
                except Timeout:
                    await asyncio.sleep(0.1)
        try:
            yield
        finally:
            lock.release()
    except TimeoutError as exc:
        raise ProviderError("다른 ChatGPT 인증 작업이 진행 중입니다.", retryable=False) from exc
