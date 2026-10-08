"""하네스가 내보내는 내부 이벤트(m1.md §4). ACP 변환은 `acp/mapping.py`가 한다(protocol.md §3)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from perdix.tools.base import ToolKind, ToolOutput

TurnStopReason = Literal["end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"]
Verification = Literal["passed", "failed", "skipped"]
PermissionOptionKind = Literal["allow_once", "allow_always", "reject_once", "reject_always"]


class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ThoughtDelta(BaseModel):
    type: Literal["thought_delta"] = "thought_delta"
    text: str


class ToolStarted(BaseModel):
    """tool_use 입력이 스트림에서 완성된 시점에 보낸다(ACP `tool_call{status: pending}`)."""

    type: Literal["tool_started"] = "tool_started"
    tool_use_id: str
    name: str
    kind: ToolKind
    title: str
    input: dict[str, Any]


class ToolRunning(BaseModel):
    type: Literal["tool_running"] = "tool_running"
    tool_use_id: str


class ToolFinished(BaseModel):
    type: Literal["tool_finished"] = "tool_finished"
    tool_use_id: str
    output: ToolOutput


class ToolFailed(BaseModel):
    type: Literal["tool_failed"] = "tool_failed"
    tool_use_id: str
    reason: str


class ContextUsage(BaseModel):
    type: Literal["context_usage"] = "context_usage"
    used: int
    size: int


class TurnEnded(BaseModel):
    type: Literal["turn_ended"] = "turn_ended"
    stop_reason: TurnStopReason
    verification: Verification | None = None


HarnessEvent = Annotated[
    TextDelta
    | ThoughtDelta
    | ToolStarted
    | ToolRunning
    | ToolFinished
    | ToolFailed
    | ContextUsage
    | TurnEnded,
    Field(discriminator="type"),
]


def _all_options() -> list[PermissionOptionKind]:
    return ["allow_once", "allow_always", "reject_once", "reject_always"]


class PermissionRequest(BaseModel):
    """`ask_permission` 콜백 인자. ACP `session/request_permission`으로 변환된다."""

    tool_use_id: str
    name: str
    kind: ToolKind
    title: str
    input: dict[str, Any]
    options: list[PermissionOptionKind] = Field(default_factory=_all_options)


class PermissionOutcome(BaseModel):
    outcome: Literal["allow_once", "allow_always", "reject_once", "reject_always", "cancelled"]
