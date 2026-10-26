from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from perdix.config.auth_profiles import (
    AuthMethod,
    AuthProfile,
    AuthProfileError,
    AuthProfiles,
    AuthProvider,
    private_write,
)
from perdix.llm.openai_credentials import credential_lock, oauth_path
from perdix.llm.openai_oauth import OpenAiOAuth
from perdix.sandbox.local import LocalBackend


class AuthCommand(BaseModel):
    model_config = ConfigDict(frozen=True)
    action: Literal["login", "logout", "status", "list", "use"]
    provider: AuthProvider = "anthropic"
    profile: str | None = None
    api_key: str | None = None


async def _ant(action: str, profile: AuthProfile) -> str:
    result = await LocalBackend().run_argv(
        ["ant", "auth", action, "--profile", f"perdix-{profile.name}"],
        cwd=Path.cwd(), timeout=300, cancel=asyncio.Event(),
    )
    if result.exit_code != 0 or result.timed_out or result.cancelled:
        raise AuthProfileError("ant 인증 실패. ant 설치 및 브라우저 로그인을 확인하세요")
    return result.stdout


async def run_profile_command(profiles: AuthProfiles, command: AuthCommand) -> str:
    if command.action in ("login", "logout") and command.profile is not None:
        identity = AuthProfile(name=command.profile, provider=command.provider, method="api_key")
        lock_path = profiles.credential_path(identity).with_name("operation")
        async with credential_lock(lock_path):
            return await _run_profile_command(profiles, command)
    return await _run_profile_command(profiles, command)


async def _run_profile_command(profiles: AuthProfiles, command: AuthCommand) -> str:
    registry = profiles.read()
    if command.action == "list":
        rows = [profiles.describe(p.provider, p.name) for p in registry.profiles.values()]
        defaults = profiles.defaults()
        rows.extend(f"기본 {provider}: {name}" for provider, name in defaults.items())
        return "\n".join(rows)
    if command.profile is None:
        if command.action == "status":
            return profiles.describe(command.provider, profiles.defaults()[command.provider])
        raise AuthProfileError("--profile <이름>이 필요합니다")
    if command.action == "use":
        profiles.use(command.provider, command.profile)
        return f"새 대화 기본 인증: {command.provider} · {command.profile}"
    if command.action == "status":
        return profiles.describe(command.provider, command.profile)
    if command.action == "logout":
        profile = registry.get_profile(command.provider, command.profile)
        if profile is None:
            raise AuthProfileError("legacy 로그아웃은 기존 auth logout 명령을 사용하세요")
        profiles.save(profile.model_copy(update={"enabled": False}))
        path = profiles.credential_path(profile)
        match profile.method:
            case "api_key":
                private_write(path, "{}\n")
            case "oauth":
                if profile.provider == "openai":
                    confirmed = await OpenAiOAuth(oauth_path(path)).logout()
                    if not confirmed:
                        return (f"{profile.name}: 로컬 로그아웃 완료. "
                                "원격 해제는 확인하지 못했습니다. "
                                "ChatGPT 설정에서 Perdix 연결을 확인하세요")
                else:
                    await _ant("logout", profile)
        return f"{profile.name}: 로그아웃 완료. 대화 기록과 프로필 선택은 유지됩니다"
    method: AuthMethod = "api_key" if command.api_key is not None else "oauth"
    profile = AuthProfile(name=command.profile, provider=command.provider,
                          method=method, enabled=False)
    if command.api_key is not None and not command.api_key.strip():
        raise AuthProfileError("API 키가 비어 있습니다")
    profiles.save(profile)
    path = profiles.credential_path(profile)
    match method:
        case "api_key":
            private_write(path, json.dumps({profile.provider: {"api_key": command.api_key}}))
        case "oauth":
            if profile.provider == "openai":
                await OpenAiOAuth(oauth_path(path)).login()
            else:
                await _ant("login", profile)
    profiles.save(profile.model_copy(update={"enabled": True}))
    return f"{profile.name}: {profile.provider} · {method} 저장 완료"
