"""공급자 이름 + 모델 → `Provider` 인스턴스. 에이전트와 LLM 중계(relay)가 공유한다."""

from __future__ import annotations

from pathlib import Path

from perdix.config.credentials import Credentials
from perdix.llm.anthropic import AnthropicProvider
from perdix.llm.base import Provider
from perdix.llm.gemini import GeminiProvider
from perdix.llm.openai import OpenAIProvider
from perdix.llm.openrouter import OpenRouterProvider


def build_provider(
    name: str, model: str, *, credentials: Credentials, credentials_path: Path
) -> Provider:
    if name == "openai":
        return OpenAIProvider(model, credentials_path=credentials_path)
    if name == "gemini":
        return GeminiProvider(model, api_key=credentials.api_key("gemini"))
    if name == "openrouter":
        return OpenRouterProvider(model, api_key=credentials.api_key("openrouter"))
    return AnthropicProvider(
        model, api_key=credentials.api_key("anthropic"),
        auth_token=credentials.auth_token("anthropic"),
    )
