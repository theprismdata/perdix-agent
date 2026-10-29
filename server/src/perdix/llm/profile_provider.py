from __future__ import annotations

from perdix.config.auth_profiles import AuthProfileError, AuthProfiles
from perdix.config.credentials import load_credentials
from perdix.llm.anthropic import AnthropicProvider
from perdix.llm.base import Provider, ProviderError
from perdix.llm.gemini import GeminiProvider
from perdix.llm.openai import OpenAIProvider
from perdix.llm.openrouter import OpenRouterProvider


def profile_provider(profiles: AuthProfiles, model: str, provider_name: str,
                     selection: str) -> Provider:
    try:
        profile = profiles.read().get_profile(provider_name, selection)
        if profile is not None and not profiles.ready(profile):
            raise AuthProfileError(f"{selection}: 재인증이 필요합니다")
    except AuthProfileError as exc:
        raise ProviderError(str(exc), retryable=False) from exc
    path = profiles.credential_path(profile) if profile else profiles.credentials_path
    credentials = load_credentials(path)
    env: dict[str, str] | None = {} if profile else None
    if profile is not None and profile.method == "api_key":
        if not credentials.api_key(provider_name, env={}):
            raise ProviderError(f"{selection}: API 키 재등록이 필요합니다", retryable=False)
    match provider_name:
        case "openai":
            return OpenAIProvider(model, api_key=credentials.api_key("openai", env=env),
                                  credentials_path=path, use_environment=profile is None)
        case "gemini":
            return GeminiProvider(model, api_key=credentials.api_key("gemini", env=env))
        case "openrouter":
            return OpenRouterProvider(model, api_key=credentials.api_key("openrouter", env=env))
        case "anthropic":
            return AnthropicProvider(
                model, api_key=credentials.api_key("anthropic", env=env),
                auth_token=credentials.auth_token("anthropic", env=env),
                profile=f"perdix-{profile.name}"
                if profile is not None and profile.method == "oauth" else None,
            )
        case _:
            raise ProviderError("지원하지 않는 인증 제공자입니다", retryable=False)
