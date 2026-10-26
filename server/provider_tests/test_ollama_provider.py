import pytest
from openai.types.responses import ResponseUsage
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

from perdix.llm.base import ModelRequest, ProviderError
from perdix.llm.ollama import OllamaProvider
from perdix.llm.openai import _map_usage


def test_usage_accepts_ollama_missing_cache_write_tokens() -> None:
    raw = ResponseUsage(
        input_tokens=12,
        input_tokens_details=InputTokensDetails.model_construct(
            cached_tokens=0, cache_write_tokens=None,
        ),
        output_tokens=7,
        output_tokens_details=OutputTokensDetails(reasoning_tokens=3),
        total_tokens=19,
    )

    usage = _map_usage(raw)

    assert usage.input_tokens == 12
    assert usage.output_tokens == 7
    assert usage.cache_write == 0


@pytest.mark.asyncio
async def test_missing_ollama_model_prompts_pull(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = OllamaProvider("gemma3:12b", base_url="http://127.0.0.1:11434/v1")

    async def missing(*args: object, **kwargs: object):
        if False:
            yield None
        raise ProviderError(
            "model 'gemma3:12b' not found", retryable=False, status=404,
        )

    monkeypatch.setattr(provider, "_stream", missing)

    with pytest.raises(ProviderError, match=r"ollama pull gemma3:12b") as caught:
        async for _ in provider.stream(ModelRequest.model_construct()):
            pass
    assert caught.value.retryable is False
    assert caught.value.status == 404
