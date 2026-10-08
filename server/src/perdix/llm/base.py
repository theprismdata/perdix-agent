"""LLM 공급자 공통 인터페이스 (m1.md §2, architecture.md §6.3).

공급자별 특이사항은 `llm/<provider>.py` 안에만 둔다. 이 모듈은 공급자 중립 타입만 정의한다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from perdix.context.messages import AssistantMessage
from perdix.context.view import ModelView


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class ModelRequest(BaseModel):
    system: str
    tools: list[ToolSpec] = Field(default_factory=list)
    view: ModelView
    max_tokens: int
    effort: str | None = None


class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ThinkingDelta(BaseModel):
    type: Literal["thinking_delta"] = "thinking_delta"
    text: str


class ToolUseStart(BaseModel):
    type: Literal["tool_use_start"] = "tool_use_start"
    id: str
    name: str


class ToolUseDelta(BaseModel):
    type: Literal["tool_use_delta"] = "tool_use_delta"
    id: str
    partial_json: str


class ToolUseEnd(BaseModel):
    type: Literal["tool_use_end"] = "tool_use_end"
    id: str
    input: dict[str, Any]


class Usage(BaseModel):
    """한 LLM 호출의 토큰 사용량(공급자 중립).

    `input_tokens`는 캐시 읽기·쓰기를 **포함한** 전체 프롬프트 토큰이다(= 컨텍스트 점유량).
    `cache_read`/`cache_write`는 그 중 캐시에서 읽은/캐시에 쓴 부분으로, 정보용 세부 값이다.
    어댑터가 공급자 형식을 이 의미로 정규화한다.
    """

    type: Literal["usage"] = "usage"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0


StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "error"]


class Stop(BaseModel):
    type: Literal["stop"] = "stop"
    reason: StopReason
    # 정상 종료 시 완성된 AssistantMessage. tool_use JSON이 잘렸으면 None.
    message: AssistantMessage | None = None
    detail: str | None = None


ModelEvent = TextDelta | ThinkingDelta | ToolUseStart | ToolUseDelta | ToolUseEnd | Usage | Stop


class ProviderError(Exception):
    """공급자 호출 실패. `retryable`이면 하네스가 백오프 재시도한다(harness-loop.md §3.5)."""

    def __init__(self, message: str, *, retryable: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class Provider(Protocol):
    name: str

    def sanitize_tool_schema(self, schema: dict[str, Any]) -> dict[str, Any]: ...

    def stream(self, req: ModelRequest) -> AsyncIterator[ModelEvent]: ...
