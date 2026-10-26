"""Anthropic Messages API 어댑터 (m1.md §2, architecture.md §6.3).

Anthropic 특이사항(cache_control, adaptive thinking, tool_result 배치, refusal)은
이 파일 안에만 둔다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import anthropic
import httpx2
from anthropic import types as at

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
    ToolSpec,
    ToolUseDelta,
    ToolUseEnd,
    ToolUseStart,
    Usage,
)

# OAuth Bearer 토큰으로 Messages API를 호출할 때 필요한 beta 플래그.
OAUTH_BETA = "oauth-2025-04-20"

_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}

_STOP_MAP: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "pause_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


def build_system(system: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": system, "cache_control": dict(_EPHEMERAL)}]


def build_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
            "eager_input_streaming": True,
        }
        for t in tools
    ]
    if out:
        out[-1]["cache_control"] = dict(_EPHEMERAL)
    return out


def _user_block(block: TextBlock | ImageBlock) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": block.media_type, "data": block.data},
    }


def _assistant_block(item: AssistantItem) -> dict[str, Any]:
    if isinstance(item, TextItem):
        return {"type": "text", "text": item.text}
    if isinstance(item, ToolUseItem):
        return {"type": "tool_use", "id": item.id, "name": item.name, "input": item.input}
    # OpaqueItem: 원본 그대로 회신(thinking/redacted_thinking 서명 보존).
    return item.raw


def build_messages(view: ModelView) -> list[dict[str, Any]]:
    """ModelView → Anthropic messages. 연속된 user 메시지는 하나로 합친다(tool_result가 맨 앞)."""
    out: list[dict[str, Any]] = []
    for msg in view.messages:
        if isinstance(msg, AssistantMessage):
            out.append({"role": "assistant", "content": [_assistant_block(i) for i in msg.items]})
            continue
        if isinstance(msg, ToolResultsGroup):
            blocks: list[dict[str, Any]] = [
                {
                    "type": "tool_result",
                    "tool_use_id": r.tool_use_id,
                    "content": r.content,
                    "is_error": r.is_error,
                }
                for r in sorted(msg.results, key=lambda r: r.index)
            ]
        elif isinstance(msg, UserMessage):
            blocks = [_user_block(b) for b in msg.content]
        else:  # pragma: no cover - ModelView 타입 밖
            raise TypeError(f"unsupported message: {type(msg).__name__}")
        if out and out[-1]["role"] == "user":
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": "user", "content": blocks})
    # 대화 캐시 브레이크포인트: 마지막 user 메시지의 마지막 블록.
    # system·마지막 tool과 합쳐 3개(API 상한 4).
    for m in reversed(out):
        if m["role"] == "user":
            if m["content"]:
                m["content"][-1]["cache_control"] = dict(_EPHEMERAL)
            break
    return out


def build_params(req: ModelRequest, model: str) -> dict[str, Any]:
    """messages.stream() 인자. 순서: tools → system → messages(캐시 프리픽스 안정화)."""
    params: dict[str, Any] = {"model": model, "max_tokens": req.max_tokens}
    tools = build_tools(req.tools)
    if tools:
        params["tools"] = tools
    params["system"] = build_system(req.system)
    params["messages"] = build_messages(req.view)
    if _supports_adaptive_thinking(model):
        params["thinking"] = {"type": "adaptive"}
        if req.effort is not None:
            params["output_config"] = {"effort": req.effort}
    return params


def _supports_adaptive_thinking(model: str) -> bool:
    """카탈로그에 없는 모델은 최신 세대로 간주한다. Haiku 4.5는 adaptive/effort를 받지 않는다."""
    try:
        return catalog.get(model).supports_effort
    except KeyError:
        return True


# 스트림 도중 SSE `error` 이벤트로 온 오류 중 재시도할 유형.
_RETRYABLE_ERROR_TYPES = frozenset({"overloaded_error", "api_error", "rate_limit_error"})


def _body_error_type(body: object) -> str | None:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and isinstance(err.get("type"), str):
            return str(err["type"])
    return None


def map_exception(exc: Exception) -> ProviderError:
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        if status < 400:
            # SDK는 스트림 중 SSE `error` 이벤트를 HTTP 200 응답으로 APIStatusError를 만든다.
            # 상태 코드 대신 본문의 error.type으로 판정한다.
            retryable = _body_error_type(exc.body) in _RETRYABLE_ERROR_TYPES
        else:
            retryable = status == 429 or status >= 500
        return ProviderError(str(exc), retryable=retryable, status=status)
    if isinstance(exc, (httpx2.TransportError, httpx2.StreamError)):
        # anthropic 1.11은 스트림 순회 중 전송 오류를 AnthropicError로 감싸지 않는다.
        return ProviderError(str(exc), retryable=True, status=None)
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError(str(exc), retryable=True, status=None)
    return ProviderError(str(exc), retryable=False, status=None)


class _Block:
    """스트리밍 중 누적되는 content block 하나."""

    def __init__(self, type_: str, start: dict[str, Any]) -> None:
        self.type = type_
        self.start = start
        self.text = ""
        self.thinking = ""
        self.signature = ""
        self.partial_json = ""
        self.input: dict[str, Any] | None = None


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        model: str,
        client: anthropic.AsyncAnthropic | None = None,
        *,
        api_key: str | None = None,
        auth_token: str | None = None,
        profile: str | None = None,
    ) -> None:
        self.model = model
        self._client = client
        self._api_key = api_key
        self._auth_token = auth_token
        self._profile = profile

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            # 재시도는 하네스가 첫 청크 기준으로 소유하므로(harness-loop.md §3.5)
            # SDK 자체 재시도를 끈다. 켜 두면 재시도 횟수가 곱해진다.
            if self._profile is not None:
                self._client = anthropic.AsyncAnthropic(max_retries=0, profile=self._profile)
            elif self._auth_token:
                # OAuth 토큰은 Bearer로 보내고, oauth beta 헤더가 있어야 받아들여진다.
                # 키와 토큰이 둘 다 있으면 토큰을 쓴다(구독 과금 경로 우선).
                self._client = anthropic.AsyncAnthropic(
                    max_retries=0,
                    auth_token=self._auth_token,
                    default_headers={"anthropic-beta": OAUTH_BETA},
                )
            else:
                # 키는 env(ANTHROPIC_API_KEY) 또는 credentials.json에서 온 값.
                self._client = anthropic.AsyncAnthropic(
                    max_retries=0, api_key=self._api_key
                )
        return self._client

    def sanitize_tool_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        # Anthropic은 JSON Schema 전체를 허용한다. additionalProperties를 덧붙이지 않는다.
        return schema

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        params = build_params(req, self.model)
        blocks: dict[int, _Block] = {}
        usage = Usage()
        raw_stop: str | None = None
        refusal_category: str | None = None
        try:
            async with self.client.messages.stream(**params) as stream:
                async for event in stream:
                    if isinstance(event, at.RawMessageStartEvent):
                        u = event.message.usage
                        usage.input_tokens = u.input_tokens
                        usage.cache_read = u.cache_read_input_tokens or 0
                        usage.cache_write = u.cache_creation_input_tokens or 0
                    elif isinstance(event, at.RawContentBlockStartEvent):
                        cb = event.content_block
                        block = _Block(cb.type, cb.model_dump(mode="json", exclude_none=True))
                        blocks[event.index] = block
                        if isinstance(cb, at.ToolUseBlock):
                            yield ToolUseStart(id=cb.id, name=cb.name)
                    elif isinstance(event, at.RawContentBlockDeltaEvent):
                        block = blocks[event.index]
                        delta = event.delta
                        if isinstance(delta, at.TextDelta):
                            block.text += delta.text
                            yield TextDelta(text=delta.text)
                        elif isinstance(delta, at.ThinkingDelta):
                            block.thinking += delta.thinking
                            yield ThinkingDelta(text=delta.thinking)
                        elif isinstance(delta, at.SignatureDelta):
                            block.signature += delta.signature
                        elif isinstance(delta, at.InputJSONDelta):
                            block.partial_json += delta.partial_json
                            yield ToolUseDelta(
                                id=str(block.start["id"]), partial_json=delta.partial_json
                            )
                    elif isinstance(event, at.RawContentBlockStopEvent):
                        block = blocks[event.index]
                        if block.type == "tool_use":
                            try:
                                parsed = json.loads(block.partial_json or "{}")
                            except json.JSONDecodeError:
                                # max_tokens로 잘린 입력. ToolUseEnd 없이 Stop에서 처리한다.
                                continue
                            block.input = parsed if isinstance(parsed, dict) else {}
                            yield ToolUseEnd(id=str(block.start["id"]), input=block.input)
                    elif isinstance(event, at.RawMessageDeltaEvent):
                        if event.delta.stop_reason is not None:
                            raw_stop = event.delta.stop_reason
                        details = event.delta.stop_details
                        if details is not None:
                            refusal_category = details.category
                        u2 = event.usage
                        usage.output_tokens = u2.output_tokens
                        if u2.input_tokens is not None:
                            usage.input_tokens = u2.input_tokens
                        if u2.cache_read_input_tokens is not None:
                            usage.cache_read = u2.cache_read_input_tokens
                        if u2.cache_creation_input_tokens is not None:
                            usage.cache_write = u2.cache_creation_input_tokens
                    # text/thinking/input_json/signature/message_stop 등 SDK 파생 이벤트는 무시.
        except ProviderError:
            raise
        except (anthropic.AnthropicError, httpx2.TransportError, httpx2.StreamError) as exc:
            raise map_exception(exc) from exc
        except TypeError as exc:
            # SDK는 키가 전혀 없을 때 요청 헤더를 만들다 TypeError를 낸다. 설정 방법을 알려 준다.
            if "authentication method" not in str(exc):
                raise
            raise ProviderError(
                "API key missing: set ANTHROPIC_API_KEY or ~/.perdix/credentials.json "
                '({"anthropic": {"api_key": "sk-ant-..."}}, chmod 600)',
                retryable=False,
                status=None,
            ) from exc

        # 공급자 중립 Usage: input_tokens는 캐시 포함 전체 프롬프트 토큰(base.Usage 참조).
        usage.input_tokens += usage.cache_read + usage.cache_write
        yield usage
        if raw_stop == "model_context_window_exceeded":
            # M1에서는 하네스가 Stop(error)를 비재시도 ProviderError로 끝낸다. 압축은 M4.
            yield Stop(reason="error", message=None, detail="context_window_exceeded")
            return
        reason: StopReason = _STOP_MAP.get(raw_stop or "end_turn", "end_turn")
        ordered = [blocks[i] for i in sorted(blocks)]
        if reason == "max_tokens" and any(b.type == "tool_use" for b in ordered):
            yield Stop(reason="max_tokens", message=None, detail="truncated_tool_use")
            return
        message = AssistantMessage(
            role="assistant",
            items=self._items(ordered),
            provider=self.name,
            model=self.model,
            stop_reason=reason,
        )
        detail = refusal_category if reason == "refusal" else None
        yield Stop(reason=reason, message=message, detail=detail)

    def _items(self, ordered: list[_Block]) -> list[AssistantItem]:
        items: list[AssistantItem] = []
        for b in ordered:
            if b.type == "text":
                items.append(TextItem(kind="text", text=b.text))
            elif b.type == "tool_use":
                items.append(
                    ToolUseItem(
                        kind="tool_use",
                        id=str(b.start["id"]),
                        name=str(b.start["name"]),
                        input=b.input or {},
                    )
                )
            elif b.type == "thinking":
                raw = {"type": "thinking", "thinking": b.thinking, "signature": b.signature}
                items.append(
                    OpaqueItem(kind="thinking", provider=self.name, model=self.model, raw=raw)
                )
            elif b.type == "redacted_thinking":
                items.append(
                    OpaqueItem(kind="thinking", provider=self.name, model=self.model, raw=b.start)
                )
        return items
