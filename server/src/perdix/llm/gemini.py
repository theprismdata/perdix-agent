"""Gemini 네이티브 REST 스트림을 공급자 중립 메시지로 변환한다."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from copy import deepcopy
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from perdix.context.messages import (
    AssistantItem,
    AssistantMessage,
    OpaqueItem,
    TextBlock,
    TextItem,
    ToolResultsGroup,
    ToolUseItem,
    UserMessage,
)
from perdix.context.view import ModelView
from perdix.llm.base import (
    ModelEvent,
    ModelRequest,
    ProviderError,
    Stop,
    StopReason,
    TextDelta,
    ThinkingDelta,
    ToolUseEnd,
    ToolUseStart,
    Usage,
)


class _Call(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    id: str | None = None


class _Part(BaseModel):
    model_config = ConfigDict(extra="allow")
    text: str | None = None
    thought: bool = False
    function_call: _Call | None = Field(default=None, alias="functionCall")


class _Content(BaseModel):
    parts: list[_Part] = Field(default_factory=list)


class _Candidate(BaseModel):
    content: _Content | None = None
    finish_reason: str | None = Field(default=None, alias="finishReason")


class _Chunk(BaseModel):
    candidates: list[_Candidate] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict, alias="usageMetadata")
    prompt_feedback: dict[str, Any] = Field(default_factory=dict, alias="promptFeedback")
    error: dict[str, Any] | None = None


def build_contents(view: ModelView, model: str) -> list[dict[str, Any]]:
    """동일 모델 원본 Part를 재생하고 다른 모델 도구 이력은 텍스트로 전달한다."""
    contents: list[dict[str, Any]] = []
    calls: dict[str, tuple[str, str | None]] = {}
    for message in view.messages:
        parts: list[dict[str, Any]] = []
        role = "user"
        if isinstance(message, UserMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append({"text": block.text})
                else:
                    parts.append({"inlineData": {
                        "mimeType": block.media_type, "data": block.data,
                    }})
        elif isinstance(message, AssistantMessage):
            role = "model"
            native = next((item for item in message.items if isinstance(item, OpaqueItem)
                           and item.provider == "gemini" and item.model == model
                           and "parts" in item.raw), None)
            if native is not None:
                parts = deepcopy(native.raw["parts"])
                calls.update({key: (value["name"], value.get("id"))
                              for key, value in native.raw.get("calls", {}).items()})
            else:
                for item in message.items:
                    if isinstance(item, TextItem):
                        parts.append({"text": item.text})
                    elif isinstance(item, ToolUseItem):
                        parts.append({"text": f"도구 호출 {item.id}: {item.name} "
                                      + json.dumps(item.input, ensure_ascii=False)})
        elif isinstance(message, ToolResultsGroup):
            for result in message.results:
                call = calls.get(result.tool_use_id)
                if call is None:
                    parts.append({"text": f"도구 결과 {result.tool_use_id}: "
                                  + ("오류: " if result.is_error else "") + result.content})
                else:
                    name, call_id = call
                    response: dict[str, Any] = {
                        "name": name,
                        "response": {"error" if result.is_error else "result": result.content},
                    }
                    if call_id is not None:
                        response["id"] = call_id
                    parts.append({"functionResponse": response})
        if not parts:
            continue
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})
    return contents


async def _chunks(response: httpx2.Response) -> AsyncIterator[_Chunk]:
    data: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            yield _Chunk.model_validate_json("\n".join(data))
            data.clear()
    if data:
        yield _Chunk.model_validate_json("\n".join(data))


class GeminiProvider:
    name = "gemini"

    def __init__(
        self, model: str, *, api_key: str | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._transport = transport

    def sanitize_tool_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(schema)

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        if not self._api_key:
            raise ProviderError(
                "Gemini 인증이 없습니다. GEMINI_API_KEY 또는 "
                "~/.perdix/credentials.json의 gemini.api_key를 설정하세요.", retryable=False,
            )
        generation: dict[str, Any] = {"maxOutputTokens": req.max_tokens}
        if req.effort is not None:
            level = {"minimal": "LOW", "low": "LOW", "medium": "MEDIUM",
                     "high": "HIGH", "xhigh": "HIGH", "max": "HIGH"}.get(req.effort)
            if level is None:
                raise ProviderError(f"지원하지 않는 Gemini effort: {req.effort}", retryable=False)
            generation["thinkingConfig"] = {"thinkingLevel": level, "includeThoughts": True}
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": req.system}]},
            "contents": build_contents(req.view, self.model), "generationConfig": generation,
        }
        if req.tools:
            body["tools"] = [{"functionDeclarations": [{
                "name": tool.name, "description": tool.description,
                "parametersJsonSchema": self.sanitize_tool_schema(tool.input_schema),
            } for tool in req.tools]}]
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               + quote(self.model, safe="") + ":streamGenerateContent?alt=sse")
        items: list[AssistantItem] = []
        raw_parts: list[dict[str, Any]] = []
        calls: dict[str, dict[str, str]] = {}
        finish: str | None = None
        usage: dict[str, int] = {}
        try:
            async with httpx2.AsyncClient(
                transport=self._transport, timeout=httpx2.Timeout(120.0, connect=20.0),
            ) as client:
                async with client.stream(
                    "POST", url, headers={"x-goog-api-key": self._api_key}, json=body,
                ) as response:
                    if response.status_code >= 400:
                        status = response.status_code
                        raise ProviderError(
                            f"Gemini API HTTP {status}",
                            retryable=status in {408, 429} or status >= 500, status=status,
                        )
                    async for chunk in _chunks(response):
                        if chunk.error is not None:
                            raise ProviderError("Gemini 스트림 오류", retryable=False)
                        usage.update({key: value for key, value in chunk.usage.items()
                                      if isinstance(value, int)})
                        if chunk.prompt_feedback.get("blockReason"):
                            finish = "SAFETY"
                        if not chunk.candidates:
                            continue
                        candidate = chunk.candidates[0]
                        finish = candidate.finish_reason or finish
                        for part in candidate.content.parts if candidate.content else []:
                            raw_parts.append(part.model_dump(
                                by_alias=True, exclude_unset=True,
                            ))
                            if part.function_call is not None:
                                call = part.function_call
                                call_id = call.id or f"gemini_{uuid4().hex}"
                                calls[call_id] = {"name": call.name}
                                if call.id is not None:
                                    calls[call_id]["id"] = call.id
                                items.append(ToolUseItem(
                                    id=call_id, name=call.name, input=call.args,
                                ))
                                yield ToolUseStart(id=call_id, name=call.name)
                                yield ToolUseEnd(id=call_id, input=call.args)
                            elif part.text is not None and part.thought:
                                yield ThinkingDelta(text=part.text)
                            elif part.text is not None:
                                if items and isinstance(items[-1], TextItem):
                                    items[-1].text += part.text
                                else:
                                    items.append(TextItem(text=part.text))
                                yield TextDelta(text=part.text)
        except httpx2.TransportError as exc:
            raise ProviderError("Gemini 네트워크 연결 실패", retryable=True) from exc
        except ValidationError as exc:
            raise ProviderError("Gemini 스트림 형식 오류", retryable=False) from exc
        yield Usage(
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0),
            cache_read=usage.get("cachedContentTokenCount", 0),
        )
        reason: StopReason
        if finish is None:
            yield Stop(reason="error", detail="Gemini 스트림이 종료 이벤트 없이 끊겼습니다.")
            return
        if finish == "MAX_TOKENS":
            reason = "max_tokens"
        elif finish in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}:
            reason = "refusal"
        elif finish != "STOP":
            yield Stop(reason="error", detail=f"Gemini 종료 사유: {finish}")
            return
        else:
            reason = "tool_use" if calls else "end_turn"
        if raw_parts:
            items.append(OpaqueItem(kind="signature", provider=self.name, model=self.model,
                                    raw={"parts": raw_parts, "calls": calls}))
        yield Stop(reason=reason, message=AssistantMessage(
            items=items, provider=self.name, model=self.model, stop_reason=reason,
        ))
