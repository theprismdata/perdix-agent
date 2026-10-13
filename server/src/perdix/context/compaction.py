"""완료된 이력 구간 선택과 요약용 텍스트 투영."""

from __future__ import annotations

import json
from dataclasses import dataclass

from perdix.context.messages import AssistantMessage, ToolResultsGroup, UserMessage
from perdix.context.view import ModelView, find_unpaired_tool_uses, project
from perdix.llm.base import ModelRequest
from perdix.session.store import Event


@dataclass(frozen=True, slots=True)
class CompactionPlan:
    from_seq: int
    to_seq: int
    view: ModelView


def estimate_tokens(request: ModelRequest) -> int:
    size = len(request.system.encode()) + len(request.view.model_dump_json().encode())
    size += sum(len(tool.model_dump_json().encode()) for tool in request.tools)
    return (size + 1) // 2


def select_plan(events: list[Event], provider: str, model: str) -> CompactionPlan | None:
    users = [event for event in events if event.kind == "user_message"
             and event.as_user_message().origin == "user"]
    assistants = [event for event in events if event.kind == "assistant_message"]
    messages = [event for event in events if event.kind in {"user_message", "assistant_message"}]
    if not users or not assistants or not messages:
        return None
    user_seq = users[-1].seq
    assistant_seq = assistants[-1].seq
    intervals = [(messages[0].seq, min(user_seq, assistant_seq) - 1)]
    if assistant_seq > user_seq:
        intervals.append((user_seq + 1, assistant_seq - 1))
    for start, end in intervals:
        if end < start:
            continue
        subset = [event for event in events if start <= event.seq <= end or (
            event.kind == "compaction" and start <= event.as_compaction().from_seq
            and event.as_compaction().to_seq <= end
        )]
        if find_unpaired_tool_uses(subset):
            continue
        view = project(subset, provider=provider, model=model)
        if len(view.messages) >= 2:
            return CompactionPlan(start, end, view)
    return None


def transcript(view: ModelView, tool_limit: int | None = None) -> str:
    lines: list[str] = []
    for message in view.messages:
        match message:
            case UserMessage():
                lines.append("USER: " + message.text())
                lines.extend(f"[이미지: {block.media_type}]" for block in message.content
                             if block.type == "image")
            case AssistantMessage():
                if message.text():
                    lines.append("ASSISTANT: " + message.text())
                lines.extend(f"TOOL CALL {tool.id} {tool.name}: " + json.dumps(tool.input)
                             for tool in message.tool_uses())
            case ToolResultsGroup():
                for result in message.results:
                    content = result.content
                    if tool_limit is not None and len(content) > tool_limit:
                        content = content[:tool_limit] + "\n[오래된 도구 결과 생략; 원본 DB에 보존]"
                    lines.append(
                        f"TOOL RESULT {result.tool_use_id} error={result.is_error}: {content}"
                    )
    return "\n\n".join(lines)
