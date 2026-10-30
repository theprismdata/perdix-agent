"""OpenAI chat.completions 와이어 ↔ Perdix 공급자 중립 타입 변환(순수 함수)."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

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
    Provider,
    Stop,
    TextDelta,
    ToolSpec,
    ToolUseDelta,
    ToolUseEnd,
    ToolUseStart,
    Usage,
)

DEFAULT_MAX_TOKENS = 8192
FINISH = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length",
          "refusal": "content_filter", "error": "stop"}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", "")) for p in content
            if isinstance(p, dict) and p.get("type") in ("text", "input_text")
        )
    return ""


def _args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def to_request(body: dict[str, Any], provider: Provider, *, provider_name: str) -> ModelRequest:
    model = str(body.get("model", ""))
    system: list[str] = []
    messages: list[UserMessage | AssistantMessage | ToolResultsGroup] = []
    order: dict[str, int] = {}  # 직전 assistant의 tool_use id → 등장 순서
    pending: list[ToolResult] = []

    def flush() -> None:
        if pending:
            messages.append(ToolResultsGroup(results=sorted(pending, key=lambda r: r.index)))
            pending.clear()

    for message in body.get("messages") or []:
        role = message.get("role")
        if role == "tool":
            call_id = str(message.get("tool_call_id", ""))
            pending.append(ToolResult(
                tool_use_id=call_id, index=order.get(call_id, len(pending)),
                content=_text(message.get("content")),
            ))
            continue
        flush()
        if role in ("system", "developer"):
            system.append(_text(message.get("content")))
        elif role == "user":
            messages.append(UserMessage(content=[TextBlock(text=_text(message.get("content")))]))
        elif role == "assistant":
            items: list[TextItem | ToolUseItem | OpaqueItem] = []
            if text := _text(message.get("content")):
                items.append(TextItem(text=text))
            order = {}
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                call_id = str(call.get("id", ""))
                order[call_id] = len(order)
                items.append(ToolUseItem(
                    id=call_id, name=str(function.get("name", "")),
                    input=_args(function.get("arguments")),
                ))
            messages.append(AssistantMessage(
                items=items, provider=provider_name, model=model,
                stop_reason="tool_use" if order else "end_turn",
            ))
    flush()
    tools = [
        ToolSpec(
            name=str(t["function"]["name"]), description=str(t["function"].get("description", "")),
            input_schema=provider.sanitize_tool_schema(
                t["function"].get("parameters") or {"type": "object", "properties": {}}),
        )
        for t in body.get("tools") or [] if t.get("type") == "function"
    ]
    limit = body.get("max_completion_tokens") or body.get("max_tokens") or DEFAULT_MAX_TOKENS
    return ModelRequest(
        system="\n\n".join(s for s in system if s), tools=tools,
        view=ModelView(messages=messages), max_tokens=int(limit),
    )


def _chunk(id_: str, model: str, delta: dict[str, Any], finish: str | None = None,
           usage: dict[str, int] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": id_, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}] if delta or finish
        else [],
    }
    if usage is not None:
        out["usage"] = usage
    return out


async def to_chunks(
    events: AsyncIterator[ModelEvent], *, id_: str, model: str
) -> AsyncIterator[dict[str, Any]]:
    """공급자 이벤트 → chat.completion.chunk 딕셔너리."""
    yield _chunk(id_, model, {"role": "assistant", "content": ""})
    index: dict[str, int] = {}
    streamed: set[str] = set()
    usage: dict[str, int] | None = None
    finish = "stop"
    async for event in events:
        if isinstance(event, TextDelta):
            yield _chunk(id_, model, {"content": event.text})
        elif isinstance(event, ToolUseStart):
            index[event.id] = len(index)
            yield _chunk(id_, model, {"tool_calls": [{
                "index": index[event.id], "id": event.id, "type": "function",
                "function": {"name": event.name, "arguments": ""}}]})
        elif isinstance(event, ToolUseDelta):
            streamed.add(event.id)
            yield _chunk(id_, model, {"tool_calls": [{
                "index": index[event.id], "function": {"arguments": event.partial_json}}]})
        elif isinstance(event, ToolUseEnd) and event.id not in streamed:
            yield _chunk(id_, model, {"tool_calls": [{
                "index": index[event.id],
                "function": {"arguments": json.dumps(event.input, ensure_ascii=False)}}]})
        elif isinstance(event, Usage):
            usage = {"prompt_tokens": event.input_tokens, "completion_tokens": event.output_tokens,
                     "total_tokens": event.input_tokens + event.output_tokens}
        elif isinstance(event, Stop):
            finish = FINISH.get(event.reason, "stop")
    yield _chunk(id_, model, {}, finish=finish)
    if usage is not None:
        yield _chunk(id_, model, {}, usage=usage)


def to_completion(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """스트림 청크를 모아 비스트리밍 응답으로 만든다."""
    text: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    finish, usage = "stop", None
    for chunk in chunks:
        usage = chunk.get("usage", usage)
        for choice in chunk["choices"]:
            delta = choice["delta"]
            if delta.get("content"):
                text.append(delta["content"])
            for call in delta.get("tool_calls") or []:
                slot = calls.setdefault(call["index"], {
                    "id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                slot["id"] = call.get("id") or slot["id"]
                slot["function"]["name"] += call.get("function", {}).get("name", "")
                slot["function"]["arguments"] += call.get("function", {}).get("arguments", "")
            finish = choice["finish_reason"] or finish
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text) or None}
    if calls:
        message["tool_calls"] = [calls[i] for i in sorted(calls)]
    first = chunks[0]
    return {"id": first["id"], "object": "chat.completion", "created": first["created"],
            "model": first["model"], "usage": usage or {},
            "choices": [{"index": 0, "message": message, "finish_reason": finish}]}
