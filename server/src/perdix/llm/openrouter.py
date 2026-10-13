"""OpenRouter Chat Completions 변환과 reasoning_details 재생 경계."""

from __future__ import annotations

import json
import ssl
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, assert_never

import httpx2
import truststore
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from perdix.context.messages import (
    AssistantItem,
    AssistantMessage,
    ImageBlock,
    OpaqueItem,
    TextBlock,
    TextItem,
    ToolResultsGroup,
    ToolUseItem,
    UserMessage,
)
from perdix.context.view import ModelView
from perdix.llm import catalog
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


class RemotePricing(BaseModel):
    prompt: str = "0"
    completion: str = "0"


class RemoteModel(BaseModel):
    id: str = Field(pattern=r"^[^\s\x00-\x1f\x7f]+$")
    name: str
    context_length: int = Field(gt=0)
    supported_parameters: list[str] = []
    architecture: dict[str, JsonValue] = {}
    pricing: RemotePricing = Field(default_factory=RemotePricing)
    top_provider: dict[str, JsonValue] = {}


class RemoteModels(BaseModel):
    data: list[RemoteModel]


async def list_models(
    transport: httpx2.AsyncBaseTransport | None = None,
) -> list[tuple[str, catalog.ModelInfo]]:
    try:
        async with httpx2.AsyncClient(
            transport=transport, timeout=20, trust_env=False,
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        ) as client:
            response = await client.get("https://openrouter.ai/api/v1/models")
            response.raise_for_status()
            if len(response.content) > 8_000_000:
                raise ProviderError("OpenRouter 모델 목록이 너무 큽니다.", retryable=False)
            listing = RemoteModels.model_validate_json(response.content)
        result: list[tuple[str, catalog.ModelInfo]] = []
        for entry in listing.data:
            modalities = entry.architecture.get("output_modalities")
            if "tools" not in entry.supported_parameters or not isinstance(modalities, list) \
                    or "text" not in modalities:
                continue
            maximum = entry.top_provider.get("max_completion_tokens")
            output = maximum if isinstance(maximum, int) and maximum > 0 else 4096
            info = catalog.ModelInfo(
                id=entry.id, provider="openrouter", context_window=entry.context_length,
                max_output=min(output, entry.context_length),
                input_price=float(entry.pricing.prompt) * 1_000_000,
                output_price=float(entry.pricing.completion) * 1_000_000,
                supports_effort="reasoning" in entry.supported_parameters,
            )
            result.append((entry.name, info))
        if not result:
            raise ProviderError("OpenRouter에서 도구 지원 모델을 찾지 못했습니다.", retryable=False)
        return sorted(result, key=lambda item: item[0].casefold())
    except (httpx2.HTTPError, ValidationError, ValueError) as exc:
        raise ProviderError("OpenRouter 모델 목록을 불러오지 못했습니다.", retryable=False) from exc


class _Function(BaseModel):
    name: str | None = None
    arguments: str | None = None


class _ToolDelta(BaseModel):
    index: int
    id: str | None = None
    function: _Function | None = None


class _Delta(BaseModel):
    content: str | None = None
    reasoning: str | None = None
    reasoning_details: list[dict[str, JsonValue]] | None = None
    tool_calls: list[_ToolDelta] | None = None


class _Choice(BaseModel):
    index: int = 0
    delta: _Delta = Field(default_factory=_Delta)
    finish_reason: str | None = None


class _Cache(BaseModel):
    cached_tokens: int = 0
    cache_write_tokens: int = 0


class _Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_tokens_details: _Cache | None = None


class _Error(BaseModel):
    code: int | str | None = None


class _Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)
    choices: list[_Choice] = Field(default_factory=list)
    usage: _Usage | None = None
    error: _Error | None = None


@dataclass(slots=True)
class _Call:
    """도구 인덱스별 ID·이름·JSON 조각을 스트림 종료까지 누적한다."""
    id: str = ""
    name: str = ""
    arguments: str = ""


def build_messages(view: ModelView, model: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for message in view.messages:
        match message:
            case UserMessage():
                content: list[dict[str, Any]] = []
                for block in message.content:
                    match block:
                        case TextBlock():
                            content.append({"type": "text", "text": block.text})
                        case ImageBlock():
                            content.append({"type": "image_url", "image_url": {
                                "url": f"data:{block.media_type};base64,{block.data}",
                            }})
                        case unreachable_block:
                            assert_never(unreachable_block)
                messages.append({"role": "user", "content": content})
            case AssistantMessage():
                out: dict[str, Any] = {"role": "assistant", "content": message.text() or None}
                calls = message.tool_uses()
                if calls:
                    out["tool_calls"] = [{"id": call.id, "type": "function", "function": {
                        "name": call.name, "arguments": json.dumps(call.input),
                    }} for call in calls]
                for item in message.items:
                    if isinstance(item, OpaqueItem) and item.provider == "openrouter" \
                            and item.model == model:
                        out.update(deepcopy(item.raw))
                messages.append(out)
            case ToolResultsGroup():
                messages.extend({"role": "tool", "tool_call_id": result.tool_use_id,
                                 "content": ("Error: " if result.is_error else "") + result.content}
                                for result in sorted(message.results,
                                                     key=lambda result: result.index))
            case unreachable:
                assert_never(unreachable)
    return messages


def _merge_reasoning(
    accumulated: list[dict[str, JsonValue]], details: list[dict[str, JsonValue]],
) -> None:
    for detail in details:
        identified = detail.get("index") is not None or detail.get("id") is not None
        previous = next((item for item in accumulated
                         if identified and (
                             (detail.get("index") is not None
                              and item.get("index") == detail.get("index"))
                             or (detail.get("id") is not None
                                 and item.get("id") == detail.get("id")))
                         and item.get("type") == detail.get("type")), None)
        if previous is None:
            accumulated.append(deepcopy(detail))
            continue
        for key, value in detail.items():
            old = previous.get(key)
            if key in {"text", "summary", "data", "signature"} \
                    and isinstance(old, str) and isinstance(value, str):
                previous[key] = old + value
            elif value is not None:
                previous[key] = value


async def _sse(response: httpx2.Response) -> AsyncIterator[str]:
    data: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            yield "\n".join(data)
            data.clear()
    if data:
        yield "\n".join(data)


class OpenRouterProvider:
    name = "openrouter"

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
                "OpenRouter 키가 없습니다. OPENROUTER_API_KEY 또는 "
                "credentials.json의 openrouter.api_key를 설정하세요.", retryable=False,
            )
        body: dict[str, Any] = {
            "model": self.model, "stream": True, "max_tokens": req.max_tokens,
            "messages": [{"role": "system", "content": req.system},
                         *build_messages(req.view, self.model)],
        }
        if req.tools:
            body["tools"] = [{"type": "function", "function": {
                "name": tool.name, "description": tool.description,
                "parameters": self.sanitize_tool_schema(tool.input_schema),
            }} for tool in req.tools]
            body["provider"] = {"require_parameters": True}
        try:
            supports_effort = catalog.get(self.model).supports_effort
        except KeyError:
            supports_effort = req.effort is not None
        if req.effort is not None and supports_effort:
            body["reasoning"] = {"effort": req.effort}
        text = ""
        reasoning = ""
        details: list[dict[str, JsonValue]] = []
        calls: dict[int, _Call] = {}
        usage = _Usage()
        finish: str | None = None
        done = False
        try:
            async with httpx2.AsyncClient(
                transport=self._transport, http2=True,
                verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), trust_env=False,
                limits=httpx2.Limits(max_connections=200, max_keepalive_connections=40,
                                    keepalive_expiry=30.0),
                timeout=httpx2.Timeout(connect=20.0, read=120.0, write=10.0, pool=10.0),
            ) as client:
                async with client.stream(
                    "POST", "https://openrouter.ai/api/v1/chat/completions", json=body,
                    headers={"Authorization": f"Bearer {self._api_key}",
                             "X-OpenRouter-Title": "Perdix"},
                ) as response:
                    if response.status_code >= 400:
                        status = response.status_code
                        raise ProviderError(f"OpenRouter API HTTP {status}", status=status,
                                            retryable=status in {408, 429} or status >= 500)
                    async for data in _sse(response):
                        if data == "[DONE]":
                            done = True
                            break
                        chunk = _Chunk.model_validate_json(data)
                        if chunk.error is not None:
                            code = chunk.error.code
                            error_status = code if isinstance(code, int) else None
                            raise ProviderError(
                                f"OpenRouter 스트림 오류 ({code})", status=error_status,
                                retryable=code in {408, 429, "server_error"}
                                or (error_status is not None and error_status >= 500),
                            )
                        if chunk.usage is not None:
                            usage = chunk.usage
                        for choice in chunk.choices:
                            if choice.index != 0:
                                continue
                            finish = choice.finish_reason or finish
                            delta = choice.delta
                            if delta.content:
                                text += delta.content
                                yield TextDelta(text=delta.content)
                            if delta.reasoning:
                                reasoning += delta.reasoning
                                yield ThinkingDelta(text=delta.reasoning)
                            else:
                                for detail in delta.reasoning_details or []:
                                    thought = detail.get("text") or detail.get("summary")
                                    if isinstance(thought, str):
                                        yield ThinkingDelta(text=thought)
                            _merge_reasoning(details, delta.reasoning_details or [])
                            for tool in delta.tool_calls or []:
                                call = calls.setdefault(tool.index, _Call())
                                call.id = tool.id or call.id
                                if tool.function is not None:
                                    call.name += tool.function.name or ""
                                    call.arguments += tool.function.arguments or ""
        except httpx2.TransportError as exc:
            raise ProviderError("OpenRouter 네트워크 연결 실패", retryable=True) from exc
        except ValidationError as exc:
            raise ProviderError("OpenRouter 스트림 형식 오류", retryable=False) from exc
        yield Usage(input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
                    cache_read=usage.prompt_tokens_details.cached_tokens
                    if usage.prompt_tokens_details else 0,
                    cache_write=usage.prompt_tokens_details.cache_write_tokens
                    if usage.prompt_tokens_details else 0)
        if not done or finish is None:
            yield Stop(reason="error", detail="OpenRouter 스트림이 종료 이벤트 없이 끊겼습니다.")
            return
        reason: StopReason
        match finish:
            case "stop":
                reason = "end_turn"
            case "tool_calls":
                reason = "tool_use"
            case "length":
                reason = "max_tokens"
            case "content_filter":
                reason = "refusal"
            case _:
                yield Stop(reason="error", detail=f"OpenRouter 종료 사유: {finish}")
                return
        items: list[AssistantItem] = [TextItem(text=text)] if text else []
        if details:
            items.append(OpaqueItem(kind="reasoning", provider=self.name, model=self.model,
                                    raw={"reasoning_details": details}))
        elif reasoning:
            items.append(OpaqueItem(kind="reasoning", provider=self.name, model=self.model,
                                    raw={"reasoning": reasoning}))
        if calls and reason != "tool_use":
            yield Stop(reason=reason if reason in {"max_tokens", "refusal"} else "error",
                       detail="truncated_tool_use" if reason == "max_tokens"
                       else "OpenRouter 도구 호출이 정상 종료되지 않았습니다.")
            return
        if reason == "tool_use" and not calls:
            yield Stop(reason="error", detail="OpenRouter 도구 호출이 비어 있습니다.")
            return
        tool_items: list[ToolUseItem] = []
        try:
            for index in sorted(calls):
                call = calls[index]
                parsed = json.loads(call.arguments or "{}")
                if not call.id or not call.name or not isinstance(parsed, dict):
                    yield Stop(reason="error", detail="OpenRouter 도구 입력이 불완전합니다.")
                    return
                tool_items.append(ToolUseItem(id=call.id, name=call.name, input=parsed))
        except json.JSONDecodeError:
            yield Stop(reason="error", detail="OpenRouter 도구 JSON이 불완전합니다.")
            return
        if len({tool.id for tool in tool_items}) != len(tool_items):
            yield Stop(reason="error", detail="OpenRouter 도구 호출 ID가 중복되었습니다.")
            return
        for tool_item in tool_items:
            items.append(tool_item)
            yield ToolUseStart(id=tool_item.id, name=tool_item.name)
            yield ToolUseEnd(id=tool_item.id, input=tool_item.input)
        yield Stop(reason=reason, message=AssistantMessage(
            items=items, provider=self.name, model=self.model, stop_reason=reason,
        ))
