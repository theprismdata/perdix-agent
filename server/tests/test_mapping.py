"""HarnessEvent → ACP session/update 변환 (protocol.md §3)."""

from __future__ import annotations

from pathlib import Path

from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ContentToolCallContent,
    FileEditToolCallContent,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
)

from perdix.acp.mapping import to_session_update
from perdix.context.messages import DiffDisplay, TextDisplay
from perdix.harness.events import (
    ContextUsage,
    TextDelta,
    ThoughtDelta,
    ToolFailed,
    ToolFinished,
    ToolRunning,
    ToolStarted,
    TurnEnded,
)
from perdix.sandbox.local import LocalBackend
from perdix.tools.base import ToolContext, ToolOutput
from perdix.tools.fs import WriteTool


def _wire(update: object) -> dict[str, object]:
    assert hasattr(update, "model_dump")
    dumped: dict[str, object] = update.model_dump(by_alias=True, exclude_none=True)
    return dumped


def test_text_and_thought_deltas() -> None:
    msg = to_session_update(TextDelta(text="hello"))
    assert isinstance(msg, AgentMessageChunk)
    assert msg.session_update == "agent_message_chunk"
    assert msg.content.type == "text" and msg.content.text == "hello"

    thought = to_session_update(ThoughtDelta(text="hmm"))
    assert isinstance(thought, AgentThoughtChunk)
    assert thought.session_update == "agent_thought_chunk"
    assert thought.content.text == "hmm"


def test_tool_started_is_pending_tool_call() -> None:
    update = to_session_update(
        ToolStarted(
            tool_use_id="tu_1",
            name="edit",
            kind="edit",
            title="edit a.py",
            input={"path": "a.py", "old_string": "x", "new_string": "y"},
        )
    )
    assert isinstance(update, ToolCallStart)
    wire = _wire(update)
    assert wire["sessionUpdate"] == "tool_call"
    assert wire["toolCallId"] == "tu_1"
    assert wire["kind"] == "edit"
    assert wire["status"] == "pending"
    assert wire["title"] == "edit a.py"
    assert wire["rawInput"] == {"path": "a.py", "old_string": "x", "new_string": "y"}


def test_tool_running_is_in_progress() -> None:
    update = to_session_update(ToolRunning(tool_use_id="tu_1"))
    assert isinstance(update, ToolCallProgress)
    assert update.session_update == "tool_call_update"
    assert update.status == "in_progress"
    assert update.content is None


def test_tool_finished_with_diff_and_text_display() -> None:
    output = ToolOutput(
        content="edited",
        display=[
            DiffDisplay(path="/p/a.py", old_text="x", new_text="y"),
            TextDisplay(text="note"),
        ],
    )
    update = to_session_update(ToolFinished(tool_use_id="tu_1", output=output))
    assert isinstance(update, ToolCallProgress)
    assert update.status == "completed"
    assert update.content is not None
    diff, text = update.content
    assert isinstance(diff, FileEditToolCallContent)
    assert (diff.path, diff.old_text, diff.new_text) == ("/p/a.py", "x", "y")
    assert isinstance(text, ContentToolCallContent)
    assert text.content.text == "note"


def test_tool_finished_without_display_uses_output_text() -> None:
    update = to_session_update(
        ToolFinished(tool_use_id="tu_2", output=ToolOutput(content="exit 0\nok"))
    )
    assert isinstance(update, ToolCallProgress)
    assert update.status == "completed"
    assert update.content is not None
    [block] = update.content
    assert isinstance(block, ContentToolCallContent)
    assert block.content.text == "exit 0\nok"


def test_new_file_diff_has_null_old_text() -> None:
    output = ToolOutput(content="wrote", display=[DiffDisplay(path="/p/new.txt", new_text="hi")])
    update = to_session_update(ToolFinished(tool_use_id="tu_3", output=output))
    assert isinstance(update, ToolCallProgress)
    assert update.content is not None
    [diff] = update.content
    assert isinstance(diff, FileEditToolCallContent)
    assert diff.old_text is None
    assert _wire(update)["content"] == [{"type": "diff", "path": "/p/new.txt", "newText": "hi"}]


def test_tool_failed_carries_reason() -> None:
    update = to_session_update(ToolFailed(tool_use_id="tu_1", reason="cancelled"))
    assert isinstance(update, ToolCallProgress)
    assert update.status == "failed"
    assert update.content is not None
    [block] = update.content
    assert isinstance(block, ContentToolCallContent)
    assert block.content.text == "cancelled"


def test_context_usage_is_usage_update() -> None:
    update = to_session_update(ContextUsage(used=1234, size=200_000))
    assert isinstance(update, UsageUpdate)
    assert _wire(update) == {"sessionUpdate": "usage_update", "used": 1234, "size": 200_000}


def test_turn_ended_is_not_sent() -> None:
    assert to_session_update(TurnEnded(stop_reason="end_turn")) is None


async def test_fs_tool_diff_maps_to_absolute_path(tmp_path: Path) -> None:
    ctx = ToolContext(cwd=tmp_path, sandbox=LocalBackend())
    output = await WriteTool().run(ctx, {"path": "sub/f.txt", "content": "x"})
    assert output.content.startswith("created sub/f.txt")
    update = to_session_update(ToolFinished(tool_use_id="tu_w", output=output))
    assert isinstance(update, ToolCallProgress)
    assert update.content is not None
    [diff] = update.content
    assert isinstance(diff, FileEditToolCallContent)
    assert diff.path == str((tmp_path / "sub/f.txt").resolve())
    assert Path(diff.path).is_absolute()
