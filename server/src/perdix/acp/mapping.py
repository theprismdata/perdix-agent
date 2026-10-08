"""HarnessEvent → ACP `session/update` 변환 (protocol.md §3).

`TurnEnded`는 보내지 않는다. 종료 사유는 `session/prompt` 응답이 전달한다.
"""

from __future__ import annotations

from acp.helpers import (
    start_tool_call,
    text_block,
    tool_content,
    tool_diff_content,
    update_agent_message_text,
    update_agent_thought_text,
    update_tool_call,
)
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ContentToolCallContent,
    FileEditToolCallContent,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
)

from perdix.context.messages import DiffDisplay, TextDisplay
from perdix.harness.events import (
    ContextUsage,
    HarnessEvent,
    TextDelta,
    ThoughtDelta,
    ToolFailed,
    ToolFinished,
    ToolRunning,
    ToolStarted,
    TurnEnded,
)
from perdix.tools.base import ToolOutput

MappedUpdate = (
    AgentMessageChunk | AgentThoughtChunk | ToolCallStart | ToolCallProgress | UsageUpdate
)
ToolContent = ContentToolCallContent | FileEditToolCallContent


def output_content(output: ToolOutput) -> list[ToolContent]:
    """도구 결과의 표시 콘텐츠. display가 없으면 결과 텍스트를 그대로 쓴다."""
    content: list[ToolContent] = []
    for item in output.display:
        if isinstance(item, DiffDisplay):
            content.append(tool_diff_content(item.path, item.new_text, item.old_text))
        elif isinstance(item, TextDisplay):
            content.append(tool_content(text_block(item.text)))
    if not content and output.content:
        content.append(tool_content(text_block(output.content)))
    return content


def to_session_update(event: HarnessEvent) -> MappedUpdate | None:
    """내부 이벤트 하나를 ACP 업데이트로 바꾼다. 보낼 것이 없으면 None."""
    if isinstance(event, TextDelta):
        return update_agent_message_text(event.text)
    if isinstance(event, ThoughtDelta):
        return update_agent_thought_text(event.text)
    if isinstance(event, ToolStarted):
        return start_tool_call(
            event.tool_use_id,
            event.title,
            kind=event.kind,
            status="pending",
            raw_input=event.input,
        )
    if isinstance(event, ToolRunning):
        return update_tool_call(event.tool_use_id, status="in_progress")
    if isinstance(event, ToolFinished):
        return update_tool_call(
            event.tool_use_id,
            status="failed" if event.output.is_error else "completed",
            content=output_content(event.output),
        )
    if isinstance(event, ToolFailed):
        # ACP에는 cancelled 상태가 없다. 취소도 failed + 사유 텍스트로 닫는다.
        return update_tool_call(
            event.tool_use_id,
            status="failed",
            content=[tool_content(text_block(event.reason))],
        )
    if isinstance(event, ContextUsage):
        return UsageUpdate(session_update="usage_update", used=event.used, size=event.size)
    if isinstance(event, TurnEnded):
        return None
    return None
