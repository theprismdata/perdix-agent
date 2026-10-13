from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from perdix.acp.agent import PerdixAgent
from perdix.config.credentials import load_credentials
from perdix.config.schema import ServerConfig
from perdix.context.messages import (
    AssistantMessage,
    ImageBlock,
    OpaqueItem,
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
    ThinkingDelta,
    ToolSpec,
    ToolUseEnd,
    ToolUseStart,
    Usage,
)
from perdix.llm.openrouter import OpenRouterProvider, build_messages
from perdix.memory.schema import MemoryConfig

MODEL = "openai/gpt-6-luna"


def request(view: ModelView | None = None) -> ModelRequest:
    return ModelRequest(system="기존 프롬프트", max_tokens=1000, effort="low",
                        view=view or ModelView(messages=[UserMessage.from_text("읽어줘")]),
                        tools=[ToolSpec(name="read", description="읽기", input_schema={
                            "type": "object", "properties": {"path": {"type": "string"}},
                            "required": ["path"], "additionalProperties": False,
                        })])


def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


async def collect(
    frames: list[dict[str, Any]], req: ModelRequest | None = None, *, done: bool = True,
    status: int = 200, model: str = MODEL,
) -> tuple[list[ModelEvent], list[dict[str, Any]]]:
    bodies: list[dict[str, Any]] = []

    def handler(incoming: httpx2.Request) -> httpx2.Response:
        assert str(incoming.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert incoming.headers["authorization"] == "Bearer test-key"
        bodies.append(json.loads(incoming.content))
        content = ": OPENROUTER PROCESSING\n\n" + "".join(
            "data: " + json.dumps(frame) + "\n\n" for frame in frames
        ) + ("data: [DONE]\n\n" if done else "")
        return httpx2.Response(status, text=content,
                               headers={"content-type": "text/event-stream"})

    provider = OpenRouterProvider(
        model, api_key="test-key", transport=httpx2.MockTransport(handler),
    )
    return [event async for event in provider.stream(req or request())], bodies


async def test_text_stream_final_usage_and_prompt() -> None:
    frames = [chunk({"content": "안녕", "reasoning": "검토"}),
              chunk({"content": "하세요", "tool_calls": None, "reasoning_details": None}, "stop"),
              {**chunk({"content": ""}, "stop"), "usage": {
                  "prompt_tokens": 100, "completion_tokens": 8,
                  "prompt_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 10},
              }}]
    events, bodies = await collect(frames)
    assert [event.text for event in events if isinstance(event, TextDelta)] == ["안녕", "하세요"]
    assert [event.text for event in events if isinstance(event, ThinkingDelta)] == ["검토"]
    assert [event for event in events if isinstance(event, Usage)] == [Usage(
        input_tokens=100, output_tokens=8, cache_read=20, cache_write=10,
    )]
    assert len([event for event in events if isinstance(event, Stop)]) == 1
    stop = events[-1]
    assert isinstance(stop, Stop) and stop.message and stop.message.text() == "안녕하세요"
    assert stop.message.provider == "openrouter"
    assert bodies[0]["messages"][0] == {"role": "system", "content": "기존 프롬프트"}
    assert bodies[0]["reasoning"] == {"effort": "low"}
    assert bodies[0]["provider"] == {"require_parameters": True}
    assert bodies[0]["tools"][0]["function"]["parameters"] == request().tools[0].input_schema


async def test_fragmented_parallel_tools_and_reasoning_roundtrip() -> None:
    frames = [chunk({"reasoning_details": [{
        "type": "reasoning.text", "id": "r1", "index": 0, "text": "검",
        "format": "anthropic-claude-v1",
    }], "tool_calls": [
        {"index": 1, "id": "b", "function": {"name": "read", "arguments": '{"path":'}},
        {"index": 0, "id": "a", "function": {"name": "read", "arguments": '{"path":'}},
    ]}), chunk({"reasoning_details": [
        {"type": "reasoning.text", "id": None, "index": 0, "text": "토", "signature": "SIG"},
        {"type": "reasoning.encrypted", "index": 1, "data": "ENC", "format": "google-gemini-v1"},
    ], "tool_calls": [
        {"index": 0, "function": {"arguments": '"a"}'}},
        {"index": 1, "function": {"arguments": '"b"}'}},
    ]}, "tool_calls")]
    events, _ = await collect(frames)
    assert [event.id for event in events if isinstance(event, ToolUseStart)] == ["a", "b"]
    assert [event.input for event in events if isinstance(event, ToolUseEnd)] == [
        {"path": "a"}, {"path": "b"},
    ]
    stop = events[-1]
    assert isinstance(stop, Stop) and stop.message and stop.reason == "tool_use"
    stored = AssistantMessage.model_validate_json(stop.message.model_dump_json())
    view = ModelView(messages=[UserMessage.from_text("읽기"), stored, ToolResultsGroup(results=[
        ToolResult(tool_use_id="b", index=1, content="failed", is_error=True),
        ToolResult(tool_use_id="a", index=0, content="done"),
    ])])
    messages = build_messages(view, MODEL)
    assert messages[1]["reasoning_details"][0] == {
        "type": "reasoning.text", "id": "r1", "index": 0, "text": "검토",
        "format": "anthropic-claude-v1", "signature": "SIG",
    }
    assert messages[1]["reasoning_details"][1]["data"] == "ENC"
    assert [message["tool_call_id"] for message in messages[2:]] == ["a", "b"]
    assert messages[3]["content"] == "Error: failed"
    _, bodies = await collect([chunk({"content": "끝"}, "stop")], request(view))
    assert bodies[0]["messages"][2:] == messages[1:]
    assert "reasoning_details" not in build_messages(view, "other-model")[1]


def test_foreign_reasoning_is_not_replayed_and_images_are_encoded() -> None:
    view = ModelView(messages=[
        UserMessage(content=[ImageBlock(media_type="image/png", data="YQ==")]),
        AssistantMessage(provider="openai", model=MODEL, stop_reason="tool_use", items=[
            TextItem(text="text"), ToolUseItem(id="old", name="read", input={"path": "a"}),
            OpaqueItem(kind="reasoning", provider="openai", model=MODEL,
                       raw={"reasoning": "foreign-secret"}),
        ]),
    ])
    messages = build_messages(view, MODEL)
    assert messages[0]["content"][0]["image_url"]["url"] == "data:image/png;base64,YQ=="
    assert "foreign-secret" not in json.dumps(messages)
    assert messages[1]["tool_calls"][0]["id"] == "old"


@pytest.mark.parametrize("finish,expected", [
    ("length", "max_tokens"), ("content_filter", "refusal"), ("error", "error"),
    (None, "error"), ("tool_calls", "error"),
])
async def test_finish_reason_without_tools(finish: str | None, expected: str) -> None:
    events, _ = await collect([chunk({"content": "partial"}, finish)])
    assert isinstance(events[-1], Stop) and events[-1].reason == expected


@pytest.mark.parametrize("finish", ["tool_calls", "length", "stop"])
@pytest.mark.parametrize("arguments", ['{"path":', "[]"])
async def test_truncated_or_invalid_tool_json_never_executes(finish: str, arguments: str) -> None:
    events, _ = await collect([chunk({"tool_calls": [{
        "index": 0, "id": "a", "function": {"name": "read", "arguments": arguments},
    }]}, finish)])
    assert not any(isinstance(event, (ToolUseStart, ToolUseEnd)) for event in events)
    assert isinstance(events[-1], Stop) and events[-1].message is None


async def test_disconnect_after_tool_finish_never_executes() -> None:
    events, _ = await collect([chunk({"tool_calls": [{
        "index": 0, "id": "a", "function": {"name": "read", "arguments": "{}"},
    }]}, "tool_calls")], done=False)
    assert not any(isinstance(event, ToolUseEnd) for event in events)
    assert isinstance(events[-1], Stop) and events[-1].reason == "error"


@pytest.mark.parametrize("status,retryable", [(400, False), (401, False), (402, False),
                                            (429, True), (503, True)])
async def test_http_errors(status: int, retryable: bool) -> None:
    with pytest.raises(ProviderError) as caught:
        await collect([], status=status)
    assert caught.value.status == status and caught.value.retryable == retryable


@pytest.mark.parametrize("code,retryable", [(402, False), (503, True), ("server_error", True)])
async def test_sse_errors(code: int | str, retryable: bool) -> None:
    with pytest.raises(ProviderError) as caught:
        await collect([{"error": {"code": code, "message": "error"}}])
    assert caught.value.retryable == retryable


async def test_missing_key_does_not_fall_back_to_other_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "other-provider")
    provider = OpenRouterProvider(MODEL)
    with pytest.raises(ProviderError, match="OPENROUTER_API_KEY") as caught:
        _ = [event async for event in provider.stream(request())]
    assert not caught.value.retryable


async def test_non_reasoning_model_does_not_send_effort() -> None:
    _, bodies = await collect([chunk({"content": "ok"}, "stop")], model="qwen/qwen3-coder-flash")
    assert "reasoning" not in bodies[0]


async def test_server_selects_openrouter_alias_and_env_precedes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"openrouter": {"api_key": "file-key"}}))
    credentials.chmod(0o600)
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    assert load_credentials(credentials).api_key("openrouter") == "env-key"
    config = tmp_path / "agents.json"
    config.write_text(json.dumps({
        "models": {"custom": {"provider": "openrouter", "model": "vendor/custom"}},
        "agents": {"chat": {"model": "custom"}},
    }))
    agent = PerdixAgent(ServerConfig(
        credentials_path=credentials, agents_config_path=config,
        db_path=str(tmp_path / "events.db"),
        memories=MemoryConfig(enabled=False),
    ))
    try:
        for model in [MODEL, "vendor/custom"]:
            provider = agent._provider_for(model)
            assert isinstance(provider, OpenRouterProvider) and provider._api_key == "env-key"
        session = await agent.new_session(cwd=str(tmp_path))
        assert session.config_options[0].current_value == "vendor/custom"
        await agent.set_config_option("model", session.session_id, MODEL)
        record = await (await agent._get_store()).get_session(session.session_id)
        assert record and record.provider == "openrouter" and record.model == MODEL
    finally:
        await agent.aclose()
