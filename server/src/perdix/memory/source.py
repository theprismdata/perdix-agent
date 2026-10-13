from dataclasses import dataclass

from perdix.context.messages import TextBlock
from perdix.context.view import find_unpaired_tool_uses
from perdix.memory.redaction import redact
from perdix.memory.schema import MemoryConfig
from perdix.session.store import Event, SessionRecord


def chat_setting(events: list[Event], key: str, default: bool) -> bool:
    for event in reversed(events):
        if event.kind == "config_change":
            change = event.as_config_change()
            if change.key == key and isinstance(change.value, bool):
                return change.value
    return default


@dataclass(frozen=True, slots=True)
class MemorySource:
    record: SessionRecord
    seq: int
    text: str

    @property
    def source_id(self) -> str:
        return f"{self.record.id}-{self.seq}"


def eligible_source(
    record: SessionRecord, events: list[Event], config: MemoryConfig,
) -> MemorySource | None:
    if not events or not chat_setting(events, "memory_generate", True):
        return None
    if find_unpaired_tool_uses(events):
        return None
    lines: list[str] = []
    user_turns = 0
    last_response = ""
    last_message_kind = ""
    source_seq = 0
    for event in events:
        if event.kind == "user_message":
            message = event.as_user_message()
            if message.origin == "user":
                text = "\n".join(b.text for b in message.content if isinstance(b, TextBlock))
                if text.strip():
                    user_turns += 1
                    lines.append("user: " + redact(text))
                last_message_kind = "user"
                source_seq = event.seq
        elif event.kind == "assistant_message":
            assistant = event.as_assistant_message()
            lines.append("assistant: " + redact(assistant.text()))
            last_response = assistant.stop_reason
            last_message_kind = "assistant"
            source_seq = event.seq
    if user_turns < config.min_user_turns:
        return None
    if last_message_kind != "assistant" or last_response != "end_turn":
        return None
    return MemorySource(record=record, seq=source_seq, text="\n".join(lines)[-32_000:])
