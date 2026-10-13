"""OpenAIProvider: SDK 스트림을 가짜로 바꿔 요청 형태와 이벤트 변환을 검사한다(네트워크 없음)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx2
import openai
import pytest
from openai.resources.responses.responses import AsyncResponses
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFailedEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionCallArgumentsDoneEvent,
    ResponseIncompleteEvent,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseTextDeltaEvent,
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
from perdix.llm.base import (
    ModelEvent,
    ModelRequest,
    ProviderError,
    Stop,
    TextDelta,
    ToolSpec,
    ToolUseDelta,
    ToolUseEnd,
    ToolUseStart,
    Usage,
)
from perdix.llm.openai import OpenAIProvider

REASONING_RAW = {
    "id": "rs_1",
    "type": "reasoning",
    "summary": [{"type": "summary_text", "text": "thinking it through"}],
    "encrypted_content": "ENC",
}


# ---- 합성 이벤트/응답 -------------------------------------------------------------


def _response(**overrides: Any) -> Response:
    base: dict[str, Any] = {
        "id": "resp_1",
        "created_at": 0.0,
        "model": "gpt-6.1-sol",
        "object": "response",
        "output": [],
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "status": "completed",
    }
    base.update(overrides)
    return Response.model_validate(base)


def _usage(input_tokens: int = 100, output_tokens: int = 42) -> dict[str, Any]:
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": 80, "cache_write_tokens": 20},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": input_tokens + output_tokens,
    }


def ev_item_added(output_index: int, item: dict[str, Any]) -> ResponseOutputItemAddedEvent:
    return ResponseOutputItemAddedEvent.model_validate(
        {
            "type": "response.output_item.added",
            "output_index": output_index,
            "sequence_number": 0,
            "item": item,
        }
    )


def ev_text_delta(item_id: str, text: str) -> ResponseTextDeltaEvent:
    return ResponseTextDeltaEvent.model_validate(
        {
            "type": "response.output_text.delta",
            "item_id": item_id,
            "output_index": 0,
            "content_index": 0,
            "delta": text,
            "logprobs": [],
            "sequence_number": 0,
        }
    )


def ev_args_delta(item_id: str, delta: str) -> ResponseFunctionCallArgumentsDeltaEvent:
    return ResponseFunctionCallArgumentsDeltaEvent.model_validate(
        {
            "type": "response.function_call_arguments.delta",
            "item_id": item_id,
            "output_index": 0,
            "delta": delta,
            "sequence_number": 0,
        }
    )


def ev_args_done(item_id: str, arguments: str) -> ResponseFunctionCallArgumentsDoneEvent:
    return ResponseFunctionCallArgumentsDoneEvent.model_validate(
        {
            "type": "response.function_call_arguments.done",
            "item_id": item_id,
            "output_index": 0,
            "arguments": arguments,
            "sequence_number": 0,
        }
    )


def ev_completed(response: Response) -> ResponseCompletedEvent:
    return ResponseCompletedEvent.model_validate(
        {"type": "response.completed", "sequence_number": 0, "response": response}
    )


def ev_incomplete(response: Response) -> ResponseIncompleteEvent:
    return ResponseIncompleteEvent.model_validate(
        {"type": "response.incomplete", "sequence_number": 0, "response": response}
    )


def ev_failed(response: Response) -> ResponseFailedEvent:
    return ResponseFailedEvent.model_validate(
        {"type": "response.failed", "sequence_number": 0, "response": response}
    )


class FakeStream:
    def __init__(self, events: list[Any], error: Exception | None) -> None:
        self._events = events
        self._error = error

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

    async def fake_create(self: AsyncResponses, **kwargs: Any) -> FakeStream:
        rec.calls.append(kwargs)
        if rec.raise_on_open is not None:
            raise rec.raise_on_open
        return FakeStream(rec.events, rec.error)

    monkeypatch.setattr(AsyncResponses, "create", fake_create)
    return rec


def provider(model: str = "gpt-6.1-sol") -> OpenAIProvider:
    return OpenAIProvider(model, client=openai.AsyncOpenAI(api_key="test"))


def tools() -> list[ToolSpec]:
    return [ToolSpec(name="read", description="read a file", input_schema={"type": "object"})]


def history_view() -> ModelView:
    return ModelView(
        messages=[
            UserMessage(role="user", content=[TextBlock(type="text", text="fix it")]),
            AssistantMessage(
                role="assistant",
                items=[
                    OpaqueItem(
                        kind="thinking", provider="openai", model="gpt-6.1-sol", raw=REASONING_RAW
                    ),
                    TextItem(kind="text", text="looking"),
                    ToolUseItem(kind="tool_use", id="call_1", name="read", input={"path": "a"}),
                ],
                provider="openai",
                model="gpt-6.1-sol",
                stop_reason="tool_use",
            ),
            ToolResultsGroup(
                results=[ToolResult(tool_use_id="call_1", index=0, content="A", is_error=True)]
            ),
        ]
    )


def request(effort: str | None = "high") -> ModelRequest:
    return ModelRequest(
        system="you are perdix", tools=tools(), view=history_view(), max_tokens=1000, effort=effort
    )


async def collect(p: OpenAIProvider, req: ModelRequest) -> list[ModelEvent]:
    return [e async for e in p.stream(req)]


@pytest.mark.parametrize("final_has_output", [False, True])
async def test_completed_tool_call_is_preserved_once(
    sdk: StreamRecorder, final_has_output: bool,
) -> None:
    item = {
        "id": "fc_1", "type": "function_call", "call_id": "call_1",
        "name": "switch_mode", "arguments": '{"mode":"plan"}', "status": "completed",
    }
    sdk.events = [
        ev_item_added(0, item),
        ev_args_done("fc_1", item["arguments"]),
        ResponseOutputItemDoneEvent.model_validate({
            "type": "response.output_item.done", "output_index": 0,
            "sequence_number": 2, "item": item,
        }),
        ev_completed(_response(output=[item] if final_has_output else [])),
    ]
    events = await collect(provider(), request())
    stop = next(event for event in events if isinstance(event, Stop))
    assert stop.reason == "tool_use"
    assert stop.message is not None
    assert len(stop.message.tool_uses()) == 1
    assert stop.message.tool_uses()[0].input == {"mode": "plan"}


def simple_end_turn() -> list[Any]:
    response = _response(
        output=[
            {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "ok", "annotations": []}],
            }
        ],
        usage=_usage(),
    )
    return [ev_text_delta("msg_1", "ok"), ev_completed(response)]


# ---- 요청 형태 -----------------------------------------------------------------


async def test_request_shape(sdk: StreamRecorder) -> None:
    sdk.events = simple_end_turn()
    await collect(provider(), request())
    (params,) = sdk.calls

    assert params["model"] == "gpt-6.1-sol"
    assert params["instructions"] == "you are perdix"
    assert params["max_output_tokens"] == 1000
    assert params["store"] is False
    assert params["include"] == ["reasoning.encrypted_content"]
    assert params["reasoning"] == {"effort": "high"}

    assert [t["name"] for t in params["tools"]] == ["read"]
    assert params["tools"][0]["type"] == "function"
    assert params["tools"][0]["strict"] is False

    input_items = params["input"]
    assert input_items[0] == {"role": "user", "content": [{"type": "input_text", "text": "fix it"}]}
    assert input_items[1] == REASONING_RAW
    assert input_items[2] == {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "looking"}],
    }
    assert input_items[3] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "read",
        "arguments": '{"path": "a"}',
    }
    assert input_items[4] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "Error: A",
    }


async def test_no_effort_keeps_include_omits_reasoning(sdk: StreamRecorder) -> None:
    sdk.events = simple_end_turn()
    await collect(provider(), request(effort=None))
    params = sdk.calls[0]
    assert params["include"] == ["reasoning.encrypted_content"]
    assert "reasoning" not in params


async def test_unknown_model_omits_include_and_reasoning(sdk: StreamRecorder) -> None:
    sdk.events = simple_end_turn()
    await collect(provider("some-future-model"), request())
    params = sdk.calls[0]
    assert "include" not in params
    assert "reasoning" not in params


def test_sanitize_tool_schema_passthrough() -> None:
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    assert provider().sanitize_tool_schema(schema) == schema


# ---- 이벤트 변환 ----------------------------------------------------------------


async def test_event_translation_tool_use(sdk: StreamRecorder) -> None:
    sdk.events = [
        ev_item_added(
            0,
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_9",
                "name": "read",
                "arguments": "",
            },
        ),
        ev_args_delta("fc_1", '{"path": '),
        ev_args_delta("fc_1", '"x.py"}'),
        ev_args_done("fc_1", '{"path": "x.py"}'),
        ev_completed(
            _response(
                status="completed",
                output=[
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_9",
                        "name": "read",
                        "arguments": '{"path": "x.py"}',
                    },
                    REASONING_RAW,
                ],
                usage=_usage(),
            )
        ),
    ]
    events = await collect(provider(), request())

    assert events[0] == ToolUseStart(id="call_9", name="read")
    assert events[1:3] == [
        ToolUseDelta(id="call_9", partial_json='{"path": '),
        ToolUseDelta(id="call_9", partial_json='"x.py"}'),
    ]
    assert events[3] == ToolUseEnd(id="call_9", input={"path": "x.py"})
    # input_tokens = 비캐시 100 + cache_read 80 + cache_write 20 (캐시 포함 전체)
    assert events[4] == Usage(input_tokens=200, output_tokens=42, cache_read=80, cache_write=20)
    stop = events[5]
    assert isinstance(stop, Stop)
    assert stop.reason == "tool_use"
    msg = stop.message
    assert msg is not None
    assert msg.provider == "openai"
    assert msg.model == "gpt-6.1-sol"
    tool = msg.items[0]
    assert isinstance(tool, ToolUseItem)
    assert tool.id == "call_9"
    assert tool.input == {"path": "x.py"}
    opaque = msg.items[1]
    assert isinstance(opaque, OpaqueItem)
    assert opaque.raw["encrypted_content"] == "ENC"


async def test_end_turn(sdk: StreamRecorder) -> None:
    sdk.events = simple_end_turn()
    events = await collect(provider(), request())
    assert TextDelta(text="ok") in events
    stop = events[-1]
    assert isinstance(stop, Stop)
    assert stop.reason == "end_turn"
    assert stop.message is not None
    assert stop.message.items == [TextItem(kind="text", text="ok")]


async def test_refusal_content_part(sdk: StreamRecorder) -> None:
    sdk.events = [
        ev_completed(
            _response(
                output=[
                    {
                        "type": "message",
                        "id": "msg_1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "refusal", "refusal": "can't help with that"}],
                    }
                ],
                usage=_usage(),
            )
        )
    ]
    stop = (await collect(provider(), request()))[-1]
    assert isinstance(stop, Stop)
    assert stop.reason == "refusal"
    assert stop.message is not None
    assert stop.message.items == [TextItem(kind="text", text="can't help with that")]


async def test_incomplete_max_output_tokens_text_only(sdk: StreamRecorder) -> None:
    sdk.events = [
        ev_completed(  # 이름은 completed지만 status가 incomplete인 응답으로 바꿔 보낸다
            _response(
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
                output=[
                    {
                        "type": "message",
                        "id": "msg_1",
                        "role": "assistant",
                        "status": "incomplete",
                        "content": [{"type": "output_text", "text": "partial", "annotations": []}],
                    }
                ],
                usage=_usage(),
            )
        )
    ]
    stop = (await collect(provider(), request()))[-1]
    assert isinstance(stop, Stop)
    assert stop.reason == "max_tokens"
    assert stop.detail == "max_output_tokens"
    assert stop.message is not None


async def test_incomplete_truncated_tool_use(sdk: StreamRecorder) -> None:
    sdk.events = [
        ev_item_added(
            0,
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "write",
                "arguments": "",
            },
        ),
        ev_args_delta("fc_1", '{"path": "a", "content": "ab'),
        ev_completed(
            _response(
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
                output=[
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "write",
                        "arguments": '{"path": "a", "content": "ab',
                        "status": "incomplete",
                    }
                ],
                usage=_usage(),
            )
        ),
    ]
    events = await collect(provider(), request())
    assert not any(isinstance(e, ToolUseEnd) for e in events)
    stop = events[-1]
    assert stop.reason == "max_tokens"
    assert stop.message is None
    assert stop.detail == "truncated_tool_use"


async def test_failed_response_is_error_stop(sdk: StreamRecorder) -> None:
    sdk.events = [
        ev_failed(
            _response(
                status="failed",
                error={"code": "server_error", "message": "boom"},
                usage=_usage(),
            )
        )
    ]
    stop = (await collect(provider(), request()))[-1]
    assert stop == Stop(reason="error", message=None, detail="boom")


# ---- 예외 매핑 -----------------------------------------------------------------


def _req() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.openai.com/v1/responses")


def _status(cls: type[openai.APIStatusError], code: int) -> openai.APIStatusError:
    return cls("boom", response=httpx2.Response(code, request=_req()), body=None)


@pytest.mark.parametrize(
    ("exc", "retryable", "status"),
    [
        (_status(openai.RateLimitError, 429), True, 429),
        (_status(openai.InternalServerError, 500), True, 500),
        (openai.APIConnectionError(request=_req()), True, None),
        (openai.APITimeoutError(request=_req()), True, None),
        (_status(openai.BadRequestError, 400), False, 400),
        (_status(openai.AuthenticationError, 401), False, 401),
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


def test_lazy_client_disables_sdk_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    assert OpenAIProvider("gpt-6.1-sol").client.max_retries == 0
