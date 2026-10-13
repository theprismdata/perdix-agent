from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

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
from perdix.acp.agent import PerdixAgent
from perdix.config.schema import ServerConfig
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
from perdix.llm.gemini import GeminiProvider, build_contents
from perdix.memory.schema import MemoryConfig

MODEL = "gemini-3.8-flash"


async def test_server_constructs_gemini_for_catalog_and_alias_with_credentials(
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "credentials.json"
    credentials.write_text(json.dumps({"gemini": {"api_key": "test-key"}}))
    config = tmp_path / "agents.json"
    config.write_text(json.dumps({
        "models": {"custom": {"provider": "gemini", "model": "custom-gemini"}},
        "agents": {"explore": {"model": "custom"}},
    }))
    agent = PerdixAgent(ServerConfig(
        credentials_path=credentials, agents_config_path=config,
        db_path=str(tmp_path / "events.db"), memories=MemoryConfig(enabled=False),
    ))
    try:
        for model in [MODEL, "custom-gemini"]:
            provider = agent._provider_for(model)
            assert isinstance(provider, GeminiProvider) and provider._api_key == "test-key"
            assert agent._provider_for(model) is provider
        session = await agent.new_session(cwd=str(tmp_path))
        assert any(option.value == MODEL for option in session.config_options[0].options)
        await agent.set_config_option(config_id="model", session_id=session.session_id, value=MODEL)
        assert agent._sessions[session.session_id].model == MODEL
    finally:
        await agent.aclose()


def request(view: ModelView | None = None, effort: str | None = "low") -> ModelRequest:
    return ModelRequest(
        system="도구를 사용하세요.", max_tokens=1000, effort=effort,
        view=view or ModelView(messages=[UserMessage.from_text("파일을 읽어줘")]),
        tools=[ToolSpec(name="read", description="파일 읽기", input_schema={
            "type": "object", "properties": {"path": {"type": "string"}},
            "required": ["path"], "additionalProperties": False,
        })],
    )


def response(parts: list[dict[str, Any]], finish: str | None = None) -> dict[str, Any]:
    candidate: dict[str, Any] = {"content": {"role": "model", "parts": parts}}
    if finish:
        candidate["finishReason"] = finish
    return {"candidates": [candidate]}


async def collect(
    chunks: list[dict[str, Any]], req: ModelRequest | None = None, *, status: int = 200,
) -> tuple[list[ModelEvent], list[dict[str, Any]]]:
    bodies: list[dict[str, Any]] = []

    async def handler(incoming: httpx2.Request) -> httpx2.Response:
        assert incoming.headers["x-goog-api-key"] == "test-key"
        assert "test-key" not in str(incoming.url)
        assert incoming.url.path.endswith(f"/{MODEL}:streamGenerateContent")
        bodies.append(json.loads(incoming.content))
        return httpx2.Response(status, text="".join(
            "data: " + json.dumps(chunk) + "\n\n" for chunk in chunks
        ), headers={"content-type": "text/event-stream"})

    provider = GeminiProvider(MODEL, api_key="test-key", transport=httpx2.MockTransport(handler))
    return [event async for event in provider.stream(req or request())], bodies


async def test_text_thinking_usage_and_request() -> None:
    chunks = [response([{"text": "검토", "thought": True}]),
              response([{"text": "안녕"}]), response([{"text": "하세요"}], "STOP"),
              {"usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 4,
                                 "thoughtsTokenCount": 3, "cachedContentTokenCount": 2,
                                 "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 12}]}}]
    events, bodies = await collect(chunks)
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["안녕", "하세요"]
    assert [e.text for e in events if isinstance(e, ThinkingDelta)] == ["검토"]
    assert next(e for e in events if isinstance(e, Usage)) == Usage(
        input_tokens=12, output_tokens=7, cache_read=2,
    )
    stop = events[-1]
    assert isinstance(stop, Stop) and stop.reason == "end_turn" and stop.message
    assert stop.message.text() == "안녕하세요"
    assert bodies[0]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "LOW"
    assert bodies[0]["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"] == (
        request().tools[0].input_schema
    )
    assert bodies[0]["systemInstruction"]["parts"][0]["text"] == request().system


@pytest.mark.parametrize("with_id", [True, False])
async def test_parallel_tool_roundtrip_preserves_signature_without_duplicates(
    with_id: bool,
) -> None:
    parts: list[dict[str, Any]] = [
        {"functionCall": {"name": "read", "args": {"path": "a"}}, "thoughtSignature": "c2ln"},
        {"functionCall": {"name": "read", "args": {"path": "b"}}},
    ]
    if with_id:
        for index, part in enumerate(parts):
            part["functionCall"]["id"] = f"call_{index}"
    events, _ = await collect([response(parts, "STOP")])
    stop = events[-1]
    assert isinstance(stop, Stop) and stop.reason == "tool_use" and stop.message
    calls = stop.message.tool_uses()
    assert len(calls) == 2 and calls[0].id != calls[1].id
    assert [e.id for e in events if isinstance(e, ToolUseStart)] == [c.id for c in calls]
    assert [e.input for e in events if isinstance(e, ToolUseEnd)] == [{"path": "a"}, {"path": "b"}]
    stored = AssistantMessage.model_validate_json(stop.message.model_dump_json())
    view = ModelView(messages=[UserMessage.from_text("읽어줘"), stored, ToolResultsGroup(results=[
        ToolResult(tool_use_id=call.id, index=index, content=f"result_{index}", is_error=index == 1)
        for index, call in enumerate(calls)
    ])])
    contents = build_contents(view, MODEL)
    assert contents[1]["parts"] == parts
    assert contents[2]["parts"][0]["functionResponse"]["response"] == {"result": "result_0"}
    assert contents[2]["parts"][1]["functionResponse"]["response"] == {"error": "result_1"}
    assert ("id" in contents[2]["parts"][0]["functionResponse"]) == with_id
    _, bodies = await collect([response([{"text": "읽었습니다"}], "STOP")], request(view))
    assert bodies[0]["contents"] == contents


def test_foreign_or_changed_model_history_is_text_and_images_are_inline() -> None:
    view = ModelView(messages=[
        UserMessage(content=[ImageBlock(media_type="image/png", data="YQ==")]),
        AssistantMessage(provider="gemini", model="old-model", stop_reason="tool_use", items=[
            TextItem(text="확인"), ToolUseItem(id="old", name="read", input={"path": "a"}),
            OpaqueItem(kind="signature", provider="gemini", model="old-model",
                       raw={"parts": [{"thoughtSignature": "do-not-replay"}]}),
        ]),
        ToolResultsGroup(results=[ToolResult(tool_use_id="old", index=0, content="done")]),
    ])
    contents = build_contents(view, MODEL)
    assert contents[0]["parts"] == [{"inlineData": {"mimeType": "image/png", "data": "YQ=="}}]
    serialized = json.dumps(contents)
    assert "do-not-replay" not in serialized and "functionCall" not in serialized
    assert "functionResponse" not in serialized and "done" in serialized


@pytest.mark.parametrize("finish,expected", [
    ("MAX_TOKENS", "max_tokens"), ("SAFETY", "refusal"),
    ("MALFORMED_FUNCTION_CALL", "error"), (None, "error"),
])
async def test_finish_reason(finish: str | None, expected: str) -> None:
    events, _ = await collect([response([{"text": "partial"}], finish)])
    stop = events[-1]
    assert isinstance(stop, Stop) and stop.reason == expected
    if expected == "error":
        assert stop.message is None


async def test_prompt_block_is_refusal() -> None:
    events, _ = await collect([{"promptFeedback": {"blockReason": "SAFETY"}}])
    assert isinstance(events[-1], Stop) and events[-1].reason == "refusal"


@pytest.mark.parametrize("status,retryable", [(400, False), (401, False), (429, True), (503, True)])
async def test_http_errors(status: int, retryable: bool) -> None:
    with pytest.raises(ProviderError) as caught:
        await collect([], status=status)
    assert caught.value.status == status and caught.value.retryable == retryable


async def test_missing_key_does_not_call_network() -> None:
    def handler(incoming: httpx2.Request) -> httpx2.Response:
        pytest.fail("키가 없으면 네트워크를 호출하지 않아야 한다")

    provider = GeminiProvider(MODEL, transport=httpx2.MockTransport(handler))
    with pytest.raises(ProviderError, match="GEMINI_API_KEY") as caught:
        _ = [event async for event in provider.stream(request())]
    assert not caught.value.retryable


async def test_invalid_chunk_is_provider_error() -> None:
    with pytest.raises(ProviderError, match="스트림 형식 오류"):
        await collect([{"candidates": [{"content": {"parts": [{"functionCall": {"name": 42}}]}}]}])


async def test_schema_copy_does_not_modify_registry() -> None:
    schema = request().tools[0].input_schema
    sanitized = GeminiProvider(MODEL).sanitize_tool_schema(schema)
    sanitized["properties"]["path"]["type"] = "integer"
    assert schema["properties"]["path"]["type"] == "string"
