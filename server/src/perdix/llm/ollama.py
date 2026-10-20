from __future__ import annotations

from collections.abc import AsyncIterator

import openai

from perdix.llm.base import ModelEvent, ModelRequest, ProviderError
from perdix.llm.openai import OpenAIProvider


class OllamaProvider(OpenAIProvider):
    name = "ollama"

    def __init__(self, model: str, *, base_url: str) -> None:
        super().__init__(model)
        self.base_url = base_url.rstrip("/") + "/"

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        try:
            async with openai.AsyncOpenAI(
                api_key="ollama",
                base_url=self.base_url,
                max_retries=0,
            ) as client:
                async for event in self._stream(req, client, chatgpt=False):
                    yield event
        except ProviderError as exc:
            if exc.status == 404 and "model" in str(exc).casefold():
                raise ProviderError(
                    f"Ollama 모델 '{self.model}'이 설치되어 있지 않습니다. "
                    f"터미널에서 `ollama pull {self.model}`을 실행한 뒤 다시 시도하세요.",
                    retryable=False,
                    status=404,
                ) from exc
            raise
