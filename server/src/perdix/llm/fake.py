"""테스트용 스크립트 공급자 (m1.md §2).

스크립트 항목 형식:
- 정상 응답: {"text": str, "tool_uses": [{"id", "name", "input"}],
  "stop": "end_turn|tool_use|max_tokens|refusal", "truncated_tool_use": bool,
  "thinking_raw": {...}, "detail": str}
- 오류: {"error": {"retryable": bool, "status": int, "after_first_chunk": bool}}
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

from perdix.context.messages import (
    AssistantItem,
    AssistantMessage,
    OpaqueItem,
    TextItem,
    ToolUseItem,
)
from perdix.llm.base import (
    ModelEvent,
    ModelRequest,
    ProviderError,
    Stop,
    StopReason,
    TextDelta,
    ThinkingDelta,
    ToolUseDelta,
    ToolUseEnd,
    ToolUseStart,
    Usage,
)


class FakeProvider:
    """스크립트된 응답을 순서대로 내보내고, 받은 요청을 `requests`에 기록한다."""

    def __init__(
        self,
        responses: list[dict[str, Any]],
        *,
        name: str = "fake",
        model: str = "fake-model",
    ) -> None:
        self.name = name
        self.model = model
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    @classmethod
    def from_file(cls, path: str | Path, **kwargs: Any) -> FakeProvider:
        """JSON 파일에서 생성. 최상위는 리스트이거나 {"responses": [...]} 객체."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data["responses"]
        if not isinstance(data, list):
            raise ValueError(f"fake script must be a list of responses: {path}")
        return cls(cast(list[dict[str, Any]], data), **kwargs)

    def sanitize_tool_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        return schema

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(req)
        index = len(self.requests) - 1
        if index >= len(self.responses):
            raise RuntimeError(f"FakeProvider script exhausted at request #{index + 1}")
        resp = self.responses[index]

        error = resp.get("error")
        if error is not None:
            if error.get("after_first_chunk", False):
                yield TextDelta(text=resp.get("text", "partial"))
            raise ProviderError(
                "fake provider error",
                retryable=bool(error.get("retryable", False)),
                status=error.get("status"),
            )

        items: list[AssistantItem] = []
        thinking_raw = resp.get("thinking_raw")
        if thinking_raw is not None:
            yield ThinkingDelta(text=str(thinking_raw.get("thinking", "")))
            items.append(
                OpaqueItem(kind="thinking", provider=self.name, model=self.model, raw=thinking_raw)
            )

        text = resp.get("text")
        if text:
            yield TextDelta(text=text)
            items.append(TextItem(kind="text", text=text))

        tool_uses: list[dict[str, Any]] = resp.get("tool_uses", [])
        truncated = bool(resp.get("truncated_tool_use", False))
        for i, tu in enumerate(tool_uses):
            payload = json.dumps(tu.get("input", {}))
            yield ToolUseStart(id=tu["id"], name=tu["name"])
            if truncated and i == len(tool_uses) - 1:
                # 입력 JSON 중간에서 끊긴다. 메시지는 커밋 대상이 아니다.
                yield ToolUseDelta(id=tu["id"], partial_json=payload[: max(1, len(payload) // 2)])
                yield Usage(input_tokens=10, output_tokens=10)
                yield Stop(reason="max_tokens", message=None, detail="truncated_tool_use")
                return
            yield ToolUseDelta(id=tu["id"], partial_json=payload)
            yield ToolUseEnd(id=tu["id"], input=tu.get("input", {}))
            items.append(
                ToolUseItem(
                    kind="tool_use", id=tu["id"], name=tu["name"], input=tu.get("input", {})
                )
            )

        default_stop: StopReason = "tool_use" if tool_uses else "end_turn"
        reason = cast(StopReason, resp.get("stop", default_stop))
        yield Usage(input_tokens=10, output_tokens=10)
        message = AssistantMessage(
            role="assistant",
            items=items,
            provider=self.name,
            model=self.model,
            stop_reason=reason,
        )
        yield Stop(reason=reason, message=message, detail=resp.get("detail"))
