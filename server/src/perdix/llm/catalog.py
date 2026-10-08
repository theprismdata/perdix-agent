"""모델 카탈로그: 컨텍스트 창, 출력 한도, 가격(USD/1M 토큰), 기능 플래그.

기준: architecture.md §6.10.
"""

from __future__ import annotations

from pydantic import BaseModel


class ModelInfo(BaseModel):
    id: str
    provider: str
    context_window: int
    max_output: int
    input_price: float
    output_price: float
    supports_effort: bool


_MODELS: dict[str, ModelInfo] = {
    m.id: m
    for m in (
        ModelInfo(
            id="claude-opus-5-5",
            provider="anthropic",
            context_window=1_000_000,
            max_output=128_000,
            input_price=4.0,
            output_price=20.0,
            supports_effort=True,
        ),
        ModelInfo(
            id="claude-sonnet-5-5",
            provider="anthropic",
            context_window=1_000_000,
            max_output=128_000,
            input_price=2.0,
            output_price=10.0,
            supports_effort=True,
        ),
        ModelInfo(
            id="claude-haiku-4-5",
            provider="anthropic",
            context_window=200_000,
            max_output=64_000,
            input_price=1.0,
            output_price=5.0,
            supports_effort=False,
        ),
        ModelInfo(
            id="gpt-6.1-sol",
            provider="openai",
            context_window=1_050_000,
            max_output=128_000,
            input_price=2.0,
            output_price=10.0,
            supports_effort=True,
        ),
        ModelInfo(
            id="gpt-6-astra",
            provider="openai",
            context_window=1_050_000,
            max_output=128_000,
            input_price=10.0,
            output_price=50.0,
            supports_effort=True,
        ),
        ModelInfo(
            id="gpt-6-luna",
            provider="openai",
            context_window=1_050_000,
            max_output=128_000,
            input_price=0.10,
            output_price=0.50,
            supports_effort=True,
        ),
    )
}


def get(model_id: str) -> ModelInfo:
    """모델 id로 조회. 모르는 id면 KeyError."""
    try:
        return _MODELS[model_id]
    except KeyError:
        raise KeyError(f"unknown model: {model_id}") from None


def all_models() -> list[ModelInfo]:
    return list(_MODELS.values())
