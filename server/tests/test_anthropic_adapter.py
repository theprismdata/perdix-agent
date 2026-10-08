"""AnthropicProvider: SDK 스트림을 가짜로 바꿔 요청 형태와 이벤트 변환을 검사한다(네트워크 없음)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.resources.messages import AsyncMessages
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
)

from perdix.context.messages import (
    AssistantMessage,
    OpaqueItem,
    TextBlock,
    TextItem,
    ToolResult,
    ToolResultsGroup,
    ToolUseItem,
    UserMessage,
)
from perdix.context.view import ModelView
from perdix.llm.anthropic import AnthropicProvider
from perdix.llm.base import (
    ModelEvent,
    ModelRequest,
    ProviderError,
    Stop,
    TextDelta,
    ThinkingDelta,
    ToolSpec,
    ToolUseDelta,
    ToolUseEnd,
    ToolUseStart,
    Usage,
)

THINKING_RAW = {"type": "thinking", "thinking": "hmm", "signature": "sig-abc"}


# ---- 합성 이벤트 -------------------------------------------------------------


def ev_start(input_tokens: int = 100) -> Any:
    return RawMessageStartEvent.model_validate(
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": 1,
                    "cache_read_input_tokens": 80,
                    "cache_creation_input_tokens": 20,
                },
            },
        }
    )


def ev_block_start(index: int, block: dict[str, Any]) -> Any:
    return RawContentBlockStartEvent.model_validate(
        {"type": "content_block_start", "index": index, "content_block": block}
    )


def ev_delta(index: int, delta: dict[str, Any]) -> Any:
    return RawContentBlockDeltaEvent.model_validate(
        {"type": "content_block_delta", "index": index, "delta": delta}
    )


def ev_block_stop(index: int) -> Any:
    return RawContentBlockStopEvent.model_validate({"type": "content_block_stop", "index": index})


def ev_message_delta(stop_reason: str, stop_details: dict[str, Any] | None = None) -> Any:
    return RawMessageDeltaEvent.model_validate(
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason,
                "stop_sequence": None,
                "stop_details": stop_details,
            },
            "usage": {"output_tokens": 42},
        }
    )


class FakeStream:
    def __init__(self, events: list[Any], error: Exception | None) -> None:
        self._events = events
        self._error = error

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[Any]:
        for e in self._events:
            yield e
        if self._error is not None:
            raise self._error


class StreamRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.events: list[Any] = []
        self.error: Exception | None = None
        self.raise_on_open: Exception | None = None


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch) -> StreamRecorder:
    rec = StreamRecorder()

    def fake_stream(self: AsyncMessages, **kwargs: Any) -> FakeStream:
        rec.calls.append(kwargs)
        if rec.raise_on_open is not None:
            raise rec.raise_on_open
        return FakeStream(rec.events, rec.error)

    monkeypatch.setattr(AsyncMessages, "stream", fake_stream)
    return rec


def provider() -> AnthropicProvider:
    return AnthropicProvider("claude-opus-5-5", client=anthropic.AsyncAnthropic(api_key="test"))


def tools() -> list[ToolSpec]:
    return [
        ToolSpec(name="read", description="read a file", input_schema={"type": "object"}),
        ToolSpec(name="bash", description="run", input_schema={"type": "object"}),
    ]


def history_view() -> ModelView:
    return ModelView(
        messages=[
            UserMessage(role="user", content=[TextBlock(type="text", text="fix it")]),
            AssistantMessage(
                role="assistant",
                items=[
                    OpaqueItem(
                        kind="thinking",
                        provider="anthropic",
                        model="claude-opus-5-5",
                        raw=THINKING_RAW,
                    ),
                    TextItem(kind="text", text="looking"),
                    ToolUseItem(kind="tool_use", id="tu_1", name="read", input={"path": "a"}),
                    ToolUseItem(kind="tool_use", id="tu_2", name="read", input={"path": "b"}),
                ],
                provider="anthropic",
                model="claude-opus-5-5",
                stop_reason="tool_use",
            ),
            ToolResultsGroup(
                results=[
                    ToolResult(tool_use_id="tu_2", index=1, content="B"),
                    ToolResult(tool_use_id="tu_1", index=0, content="A", is_error=True),
                ]
            ),
            UserMessage(
                role="user", content=[TextBlock(type="text", text="continue")], origin="synthetic"
            ),
        ]
    )


def request(effort: str | None = "xhigh") -> ModelRequest:
    return ModelRequest(
        system="you are perdix", tools=tools(), view=history_view(), max_tokens=1000, effort=effort
    )


async def collect(p: AnthropicProvider, req: ModelRequest) -> list[ModelEvent]:
    return [e async for e in p.stream(req)]


def simple_end_turn() -> list[Any]:
    return [
        ev_start(),
        ev_block_start(0, {"type": "text", "text": ""}),
        ev_delta(0, {"type": "text_delta", "text": "ok"}),
        ev_block_stop(0),
        ev_message_delta("end_turn"),
    ]


# ---- 요청 형태 -----------------------------------------------------------------


async def test_request_shape(sdk: StreamRecorder) -> None:
    sdk.events = simple_end_turn()
    await collect(provider(), request())
    (params,) = sdk.calls

    assert params["model"] == "claude-opus-5-5"
    assert params["max_tokens"] == 1000
    assert list(params)[:5] == ["model", "max_tokens", "tools", "system", "messages"]
    assert params["system"] == [
        {"type": "text", "text": "you are perdix", "cache_control": {"type": "ephemeral"}}
    ]
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"] == {"effort": "xhigh"}
    assert "fallbacks" not in params

    t = params["tools"]
    assert [x["name"] for x in t] == ["read", "bash"]
    assert all(x["eager_input_streaming"] is True for x in t)
    assert "cache_control" not in t[0]
    assert t[-1]["cache_control"] == {"type": "ephemeral"}

    msgs = params["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    # assistant items: 원래 순서, opaque raw 그대로
    a = msgs[1]["content"]
    assert a[0] == THINKING_RAW
    assert [b["type"] for b in a] == ["thinking", "text", "tool_use", "tool_use"]
    assert a[2] == {"type": "tool_use", "id": "tu_1", "name": "read", "input": {"path": "a"}}
    # tool_result가 user content 맨 앞, index 순
    u = msgs[2]["content"]
    assert [b["type"] for b in u] == ["tool_result", "tool_result", "text"]
    assert u[0] == {"type": "tool_result", "tool_use_id": "tu_1", "content": "A", "is_error": True}
    assert u[1]["tool_use_id"] == "tu_2"
    # 대화 캐시 브레이크포인트: 마지막 user 메시지의 마지막 블록에만
    assert u[2] == {"type": "text", "text": "continue", "cache_control": {"type": "ephemeral"}}
    assert all("cache_control" not in b for b in u[:2])
    assert all("cache_control" not in b for m in msgs[:2] for b in m["content"])
    breakpoints = (
        sum("cache_control" in b for b in params["system"])
        + sum("cache_control" in x for x in t)
        + sum("cache_control" in b for m in msgs for b in m["content"])
    )
    assert breakpoints == 3


async def test_no_effort_omits_output_config(sdk: StreamRecorder) -> None:
    sdk.events = simple_end_turn()
    await collect(provider(), request(effort=None))
    assert "output_config" not in sdk.calls[0]


def test_sanitize_tool_schema_passthrough() -> None:
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    out = provider().sanitize_tool_schema(schema)
    assert out == schema
    assert "additionalProperties" not in out


# ---- 이벤트 변환 ----------------------------------------------------------------


async def test_event_translation_tool_use(sdk: StreamRecorder) -> None:
    sdk.events = [
        ev_start(),
        ev_block_start(0, {"type": "thinking", "thinking": "", "signature": ""}),
        ev_delta(0, {"type": "thinking_delta", "thinking": "let me "}),
        ev_delta(0, {"type": "thinking_delta", "thinking": "think"}),
        ev_delta(0, {"type": "signature_delta", "signature": "SIG"}),
        ev_block_stop(0),
        ev_block_start(1, {"type": "text", "text": ""}),
        ev_delta(1, {"type": "text_delta", "text": "Reading"}),
        ev_block_stop(1),
        ev_block_start(2, {"type": "tool_use", "id": "tu_9", "name": "read", "input": {}}),
        ev_delta(2, {"type": "input_json_delta", "partial_json": '{"path": '}),
        ev_delta(2, {"type": "input_json_delta", "partial_json": '"x.py"}'}),
        ev_block_stop(2),
        ev_message_delta("tool_use"),
    ]
    events = await collect(provider(), request())

    assert events[:2] == [ThinkingDelta(text="let me "), ThinkingDelta(text="think")]
    assert events[2] == TextDelta(text="Reading")
    assert events[3] == ToolUseStart(id="tu_9", name="read")
    assert events[4:6] == [
        ToolUseDelta(id="tu_9", partial_json='{"path": '),
        ToolUseDelta(id="tu_9", partial_json='"x.py"}'),
    ]
    assert events[6] == ToolUseEnd(id="tu_9", input={"path": "x.py"})
    # input_tokens = 비캐시 100 + cache_read 80 + cache_write 20 (캐시 포함 전체)
    assert events[7] == Usage(input_tokens=200, output_tokens=42, cache_read=80, cache_write=20)
    stop = events[8]
    assert isinstance(stop, Stop)
    assert stop.reason == "tool_use"
    assert stop.detail is None
    msg = stop.message
    assert msg is not None
    assert msg.provider == "anthropic"
    assert msg.model == "claude-opus-5-5"
    assert msg.stop_reason == "tool_use"
    assert [i.kind for i in msg.items] == ["thinking", "text", "tool_use"]
    opaque = msg.items[0]
    assert isinstance(opaque, OpaqueItem)
    assert opaque.raw == {"type": "thinking", "thinking": "let me think", "signature": "SIG"}
    assert opaque.provider == "anthropic"
    tool = msg.items[2]
    assert isinstance(tool, ToolUseItem)
    assert tool.input == {"path": "x.py"}


async def test_redacted_thinking_kept_verbatim(sdk: StreamRecorder) -> None:
    sdk.events = [
        ev_start(),
        ev_block_start(0, {"type": "redacted_thinking", "data": "ENCRYPTED"}),
        ev_block_stop(0),
        ev_block_start(1, {"type": "text", "text": ""}),
        ev_delta(1, {"type": "text_delta", "text": "ok"}),
        ev_block_stop(1),
        ev_message_delta("end_turn"),
    ]
    events = await collect(provider(), request())
    stop = events[-1]
    assert isinstance(stop, Stop) and stop.message is not None
    first = stop.message.items[0]
    assert isinstance(first, OpaqueItem)
    assert first.raw == {"type": "redacted_thinking", "data": "ENCRYPTED"}


# ---- stop_reason 매핑 ----------------------------------------------------------


async def test_end_turn(sdk: StreamRecorder) -> None:
    sdk.events = simple_end_turn()
    stop = (await collect(provider(), request()))[-1]
    assert isinstance(stop, Stop)
    assert stop.reason == "end_turn"
    assert stop.message is not None
    assert stop.message.items == [TextItem(kind="text", text="ok")]


async def test_max_tokens_text_only_keeps_message(sdk: StreamRecorder) -> None:
    sdk.events = [*simple_end_turn()[:-1], ev_message_delta("max_tokens")]
    stop = (await collect(provider(), request()))[-1]
    assert isinstance(stop, Stop)
    assert stop.reason == "max_tokens"
    assert stop.message is not None
    assert stop.detail is None


async def test_max_tokens_truncated_tool_use(sdk: StreamRecorder) -> None:
    sdk.events = [
        ev_start(),
        ev_block_start(0, {"type": "tool_use", "id": "tu_1", "name": "write", "input": {}}),
        ev_delta(0, {"type": "input_json_delta", "partial_json": '{"path": "a", "content": "ab'}),
        ev_block_stop(0),
        ev_message_delta("max_tokens"),
    ]
    events = await collect(provider(), request())
    assert not any(isinstance(e, ToolUseEnd) for e in events)
    stop = events[-1]
    assert stop == Stop(reason="max_tokens", message=None, detail="truncated_tool_use")


async def test_refusal_detail_category(sdk: StreamRecorder) -> None:
    sdk.events = [
        *simple_end_turn()[:-1],
        ev_message_delta("refusal", {"type": "refusal", "category": "cyber", "explanation": "no"}),
    ]
    stop = (await collect(provider(), request()))[-1]
    assert isinstance(stop, Stop)
    assert stop.reason == "refusal"
    assert stop.detail == "cyber"


# ---- 예외 매핑 -----------------------------------------------------------------


def _req() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _status(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    return cls("boom", response=httpx2.Response(code, request=_req()), body=None)


@pytest.mark.parametrize(
    ("exc", "retryable", "status"),
    [
        (_status(anthropic.RateLimitError, 429), True, 429),
        (_status(anthropic.InternalServerError, 500), True, 500),
        (_status(anthropic.APIStatusError, 529), True, 529),
        (anthropic.APIConnectionError(request=_req()), True, None),
        (anthropic.APITimeoutError(request=_req()), True, None),
        (_status(anthropic.BadRequestError, 400), False, 400),
        (_status(anthropic.AuthenticationError, 401), False, 401),
    ],
)
async def test_exception_mapping_on_open(
    sdk: StreamRecorder, exc: Exception, retryable: bool, status: int | None
) -> None:
    sdk.raise_on_open = exc
    with pytest.raises(ProviderError) as info:
        await collect(provider(), request())
    assert info.value.retryable is retryable
    assert info.value.status == status
    assert info.value.__cause__ is exc


async def test_exception_mid_stream_after_first_chunk(sdk: StreamRecorder) -> None:
    sdk.events = simple_end_turn()[:3]
    sdk.error = _status(anthropic.APIStatusError, 529)
    seen: list[ModelEvent] = []
    with pytest.raises(ProviderError) as info:
        async for e in provider().stream(request()):
            seen.append(e)
    assert seen == [TextDelta(text="ok")]
    assert info.value.retryable is True
    assert info.value.status == 529


async def test_transport_error_mid_stream_is_retryable(sdk: StreamRecorder) -> None:
    sdk.events = simple_end_turn()[:3]
    err = httpx2.ReadError("connection reset", request=_req())
    sdk.error = err
    with pytest.raises(ProviderError) as info:
        await collect(provider(), request())
    assert info.value.retryable is True
    assert info.value.status is None
    assert info.value.__cause__ is err


def _sse_error(error_type: str) -> anthropic.APIStatusError:
    """SSE `error` 이벤트: SDK가 HTTP 200 응답으로 만드는 APIStatusError."""
    client = anthropic.AsyncAnthropic(api_key="test")
    body = {"type": "error", "error": {"type": error_type, "message": "x"}}
    return client._make_status_error(
        str(body), body=body, response=httpx2.Response(200, request=_req())
    )


@pytest.mark.parametrize(
    ("error_type", "retryable"),
    [
        ("overloaded_error", True),
        ("api_error", True),
        ("rate_limit_error", True),
        ("invalid_request_error", False),
    ],
)
async def test_sse_error_event_mid_stream(
    sdk: StreamRecorder, error_type: str, retryable: bool
) -> None:
    sdk.events = simple_end_turn()[:3]
    sdk.error = _sse_error(error_type)
    with pytest.raises(ProviderError) as info:
        await collect(provider(), request())
    assert info.value.status == 200
    assert info.value.retryable is retryable


async def test_context_window_exceeded_is_error_stop(sdk: StreamRecorder) -> None:
    sdk.events = [*simple_end_turn()[:-1], ev_message_delta("model_context_window_exceeded")]
    stop = (await collect(provider(), request()))[-1]
    assert stop == Stop(reason="error", message=None, detail="context_window_exceeded")


def test_lazy_client_disables_sdk_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    assert AnthropicProvider("claude-opus-5-5").client.max_retries == 0


def test_haiku_omits_thinking_and_effort() -> None:
    """Haiku 4.5는 adaptive thinking과 effort를 지원하지 않으므로 둘 다 생략한다."""
    from perdix.llm.anthropic import build_params

    req = ModelRequest(
        system="s", tools=[], view=ModelView(messages=[]), max_tokens=100, effort="high"
    )
    params = build_params(req, "claude-haiku-4-5")
    assert "thinking" not in params
    assert "output_config" not in params
    opus = build_params(req, "claude-opus-5-5")
    assert opus["thinking"] == {"type": "adaptive"}
    assert opus["output_config"] == {"effort": "high"}


async def test_missing_api_key_is_clear_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """키가 전혀 없으면 traceback 대신 설정 방법을 담은 ProviderError가 난다."""
    from perdix.llm.anthropic import AnthropicProvider

    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    provider = AnthropicProvider("claude-opus-5-5", api_key=None)
    req = ModelRequest(system="s", tools=[], view=ModelView(messages=[]), max_tokens=100)
    with pytest.raises(ProviderError) as info:
        async for _ in provider.stream(req):
            pass
    assert info.value.retryable is False
    assert "credentials.json" in str(info.value)
