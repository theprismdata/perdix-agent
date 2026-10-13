"""OpenAI Responses API 어댑터 (m1.md §2, architecture.md §6.3, §6.10).

Responses API 특이사항(stateless `store=False` + `reasoning.encrypted_content` 재생,
`function_call`/`function_call_output` 페어링, `reasoning.effort`)은 이 파일 안에만 둔다.

`function_call_output`에는 에러 전용 필드가 없다(OpenAI Python SDK
`response_input_item_param.FunctionCallOutput` 확인: `output`/`type`/`status`뿐,
`is_error` 없음). 도구 실패는 `output` 텍스트에 "Error: " 접두사로만 표시한다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import openai
from openai.types.responses import ResponseOutputItem

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

# stateless(store=False) 호출에서 추론 체인을 다음 턴에 재생하려면 매 요청에 보내야 한다.
# 처음 한 번만 보내면 이후 reasoning item이 id만 남고 encrypted_content가 비어 깨진다.
_REASONING_INCLUDE = ["reasoning.encrypted_content"]

_INCOMPLETE_TO_STOP: dict[str, StopReason] = {
    "max_output_tokens": "max_tokens",
    "content_filter": "refusal",
}


def build_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.input_schema,
            # 우리 도구 스키마가 strict 모드 요구(전 필드 required + additionalProperties:false)를
            # 항상 만족하지 않으므로 strict는 쓰지 않는다.
            "strict": False,
        }
        for t in tools
    ]


def _user_content(block: TextBlock | ImageBlock) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "input_text", "text": block.text}
    return {
        "type": "input_image",
        "image_url": f"data:{block.media_type};base64,{block.data}",
    }


def _assistant_items(msg: AssistantMessage) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in msg.items:
        if isinstance(item, TextItem):
            out.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": item.text}],
                }
            )
        elif isinstance(item, ToolUseItem):
            out.append(
                {
                    "type": "function_call",
                    "call_id": item.id,
                    "name": item.name,
                    "arguments": json.dumps(item.input),
                }
            )
        else:
            # OpaqueItem(reasoning): encrypted_content 포함 원본 그대로 재생.
            out.append(item.raw)
    return out


def build_input(view: ModelView) -> list[dict[str, Any]]:
    """ModelView → Responses API `input` 아이템 목록."""
    out: list[dict[str, Any]] = []
    for msg in view.messages:
        if isinstance(msg, AssistantMessage):
            out.extend(_assistant_items(msg))
        elif isinstance(msg, ToolResultsGroup):
            for r in sorted(msg.results, key=lambda r: r.index):
                content = f"Error: {r.content}" if r.is_error else r.content
                out.append(
                    {"type": "function_call_output", "call_id": r.tool_use_id, "output": content}
                )
        elif isinstance(msg, UserMessage):
            out.append({"role": "user", "content": [_user_content(b) for b in msg.content]})
        else:  # pragma: no cover - ModelView 타입 밖
            raise TypeError(f"unsupported message: {type(msg).__name__}")
    return out


def build_params(req: ModelRequest, model: str) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": model,
        "instructions": req.system,
        "input": build_input(req.view),
        "max_output_tokens": req.max_tokens,
        "store": False,
    }
    tools = build_tools(req.tools)
    if tools:
        params["tools"] = tools
    if _supports_effort(model):
        params["include"] = list(_REASONING_INCLUDE)
        if req.effort is not None:
            params["reasoning"] = {"effort": req.effort}
    return params


def _supports_effort(model: str) -> bool:
    """카탈로그에 없는 모델은 reasoning/include를 보내지 않는다(비추론 모델은 400)."""
    try:
        return catalog.get(model).supports_effort
    except KeyError:
        return False


def map_exception(exc: Exception) -> ProviderError:
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        retryable = status == 429 or status >= 500
        return ProviderError(str(exc), retryable=retryable, status=status)
    if isinstance(exc, openai.APIConnectionError):
        return ProviderError(str(exc), retryable=True, status=None)
    return ProviderError(str(exc), retryable=False, status=None)


def _message_items(item: Any) -> list[AssistantItem]:
    items: list[AssistantItem] = []
    for part in item.content:
        if part.type == "output_text":
            items.append(TextItem(kind="text", text=part.text))
        elif part.type == "refusal":
            items.append(TextItem(kind="text", text=part.refusal))
    return items


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        model: str,
        client: openai.AsyncOpenAI | None = None,
        *,
        api_key: str | None = None,
        credentials_path: Path | None = None,
    ) -> None:
        self.model = model
        self._client = client
        self._api_key = api_key
        self._credentials_path = credentials_path

    @property
    def client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            # 재시도는 하네스가 소유한다(AGENTS.md 하네스 루프 규칙). SDK 자체 재시도를 끈다.
            self._client = openai.AsyncOpenAI(max_retries=0, api_key=self._api_key)
        return self._client

    def sanitize_tool_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        return schema

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        from perdix.config.credentials import load_credentials
        from perdix.llm.openai_credentials import oauth_path
        from perdix.llm.openai_oauth import OpenAiOAuth

        key = self._api_key or load_credentials(self._credentials_path).api_key("openai")
        if self._client is not None:
            async for event in self._stream(req, self._client, chatgpt=False):
                yield event
            return
        chatgpt = not key
        token = key or await OpenAiOAuth(oauth_path(self._credentials_path)).access_token()
        async with openai.AsyncOpenAI(
            api_key=token,
            base_url="https://api.openai.com/v1" if chatgpt else None,
            max_retries=0,
        ) as client:
            async for event in self._stream(req, client, chatgpt=chatgpt):
                yield event

    async def _stream(
        self, req: ModelRequest, client: openai.AsyncOpenAI, *, chatgpt: bool,
    ) -> AsyncIterator[ModelEvent]:
        params = build_params(req, self.model)
        if chatgpt:
            params.pop("max_output_tokens", None)
            params["input"] = [
                {**item, "namespace": "perdix"} if item.get("type") == "function_call" else item
                for item in params["input"]
            ]
            tools = params.pop("tools", [])
            if tools:
                params["tools"] = [{
                    "type": "namespace",
                    "name": "perdix",
                    "description": "프로젝트 파일 조회·수정 및 명령 실행을 위한 Perdix 도구",
                    "tools": tools,
                }]
        usage = Usage()
        # function_call 아이템의 Responses 내부 item_id → 우리가 쓰는 id(call_id) 매핑.
        # delta/done 이벤트는 item_id만 주고 call_id는 output_item.added에서만 온다.
        call_ids: dict[str, str] = {}
        completed_items: dict[int, ResponseOutputItem] = {}
        response: Any = None
        try:
            stream = await client.responses.create(**params, stream=True)
            async for event in stream:
                if event.type == "response.output_item.added":
                    item = event.item
                    if item.type == "function_call":
                        call_ids[item.id or event.output_index.__str__()] = item.call_id
                        yield ToolUseStart(id=item.call_id, name=item.name)
                elif event.type == "response.output_item.done":
                    completed_items[event.output_index] = event.item
                elif event.type == "response.output_text.delta":
                    yield TextDelta(text=event.delta)
                elif event.type in (
                    "response.reasoning_summary_text.delta",
                    "response.reasoning_text.delta",
                ):
                    yield ThinkingDelta(text=event.delta)
                elif event.type == "response.function_call_arguments.delta":
                    call_id = call_ids.get(event.item_id)
                    if call_id is not None:
                        yield ToolUseDelta(id=call_id, partial_json=event.delta)
                elif event.type == "response.function_call_arguments.done":
                    call_id = call_ids.get(event.item_id)
                    if call_id is None:
                        continue
                    try:
                        parsed = json.loads(event.arguments or "{}")
                    except json.JSONDecodeError:
                        continue  # max_output_tokens로 잘린 입력. Stop에서 처리한다.
                    yield ToolUseEnd(id=call_id, input=parsed if isinstance(parsed, dict) else {})
                elif event.type in (
                    "response.completed",
                    "response.failed",
                    "response.incomplete",
                ):
                    response = event.response
        except ProviderError:
            raise
        except (openai.APIError, openai.APIConnectionError) as exc:
            raise map_exception(exc) from exc

        if response is None:
            raise ProviderError("stream ended without a final response event", retryable=True)

        u = response.usage
        if u is not None:
            usage.input_tokens = u.input_tokens
            usage.output_tokens = u.output_tokens
            usage.cache_read = u.input_tokens_details.cached_tokens
            usage.cache_write = u.input_tokens_details.cache_write_tokens
        usage.input_tokens += usage.cache_read + usage.cache_write
        yield usage

        if response.status == "failed":
            detail = response.error.message if response.error is not None else "unknown error"
            yield Stop(reason="error", message=None, detail=detail)
            return

        items: list[AssistantItem] = []
        has_tool_use = False
        has_refusal = False
        output = response.output or [completed_items[index] for index in sorted(completed_items)]
        for item in output:
            if item.type == "message":
                items.extend(_message_items(item))
                has_refusal = has_refusal or any(p.type == "refusal" for p in item.content)
            elif item.type == "function_call":
                has_tool_use = True
                try:
                    parsed_input = json.loads(item.arguments or "{}")
                except json.JSONDecodeError:
                    continue  # 잘린 입력. truncated_tool_use로 빠지면 message 자체를 버린다.
                items.append(
                    ToolUseItem(
                        kind="tool_use",
                        id=item.call_id,
                        name=item.name,
                        input=parsed_input if isinstance(parsed_input, dict) else {},
                    )
                )
            elif item.type == "reasoning":
                items.append(
                    OpaqueItem(
                        kind="thinking",
                        provider=self.name,
                        model=self.model,
                        raw=item.model_dump(mode="json", exclude_none=True),
                    )
                )

        if response.status == "incomplete":
            reason_id = response.incomplete_details.reason if response.incomplete_details else None
            reason = _INCOMPLETE_TO_STOP.get(reason_id or "", "max_tokens")
            if reason == "max_tokens" and has_tool_use:
                yield Stop(reason="max_tokens", message=None, detail="truncated_tool_use")
                return
            detail = reason_id
        else:
            reason = "refusal" if has_refusal else ("tool_use" if has_tool_use else "end_turn")
            detail = None

        message = AssistantMessage(
            role="assistant", items=items, provider=self.name, model=self.model, stop_reason=reason
        )
        yield Stop(reason=reason, message=message, detail=detail)
