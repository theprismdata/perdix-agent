from typing import Any

from perdix.context.messages import (
    AssistantMessage,
    OpaqueItem,
    TextItem,
    ToolResult,
    ToolUseItem,
    UserMessage,
)
from perdix.context.view import ToolResultsGroup, find_unpaired_tool_uses, project
from perdix.session.store import CompactionPayload, Event, ToolStartedPayload


class Log:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def add(self, kind: str, payload: Any) -> int:
        seq = len(self.events) + 1
        data = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
        self.events.append(
            Event(id=seq, session_id="s", seq=seq, kind=kind, payload=data, created_at=0.0)
        )
        return seq


def assistant(*items: Any, provider: str = "anthropic", model: str = "m1") -> AssistantMessage:
    return AssistantMessage(items=list(items), provider=provider, model=model, stop_reason="x")


def tu(i: str) -> ToolUseItem:
    return ToolUseItem(id=i, name="read", input={"path": i})


def test_tool_results_regrouped_by_index() -> None:
    log = Log()
    log.add("user_message", UserMessage.from_text("go"))
    log.add("assistant_message", assistant(TextItem(text="t"), tu("a"), tu("b"), tu("c")))
    for idx, tid in enumerate(["a", "b", "c"]):
        log.add("tool_started", ToolStartedPayload(tool_use_id=tid, name="read", index=idx))
    # 완료 순서: c, a, b
    log.add("tool_result", ToolResult(tool_use_id="c", index=2, content="C"))
    log.add("mode_change", {"mode": "plan"})
    log.add("tool_result", ToolResult(tool_use_id="a", index=0, content="A"))
    log.add("tool_result", ToolResult(tool_use_id="b", index=1, content="B", is_error=True))
    log.add("tool_result", ToolResult(tool_use_id="zzz", index=0, content="orphan"))
    log.add("assistant_message", assistant(TextItem(text="done")))

    view = project(log.events, provider="anthropic", model="m1")
    kinds = [type(m).__name__ for m in view.messages]
    assert kinds == ["UserMessage", "AssistantMessage", "ToolResultsGroup", "AssistantMessage"]
    group = view.messages[2]
    assert isinstance(group, ToolResultsGroup)
    assert [r.tool_use_id for r in group.results] == ["a", "b", "c"]
    assert [r.content for r in group.results] == ["A", "B", "C"]


def test_opaque_items_dropped_on_provider_or_model_mismatch() -> None:
    think = OpaqueItem(kind="thinking", provider="anthropic", model="m1", raw={"sig": "x"})
    log = Log()
    log.add("user_message", UserMessage.from_text("q"))
    log.add("assistant_message", assistant(think, TextItem(text="answer"), tu("a")))
    log.add("tool_result", ToolResult(tool_use_id="a", index=0, content="r"))
    only_thinking = OpaqueItem(kind="reasoning", provider="anthropic", model="m1", raw={})
    log.add("assistant_message", assistant(only_thinking))

    same = project(log.events, provider="anthropic", model="m1")
    am = same.messages[1]
    assert isinstance(am, AssistantMessage)
    assert am.items[0] == think  # 원래 순서 유지
    assert len(same.messages) == 4

    for provider, model in [("openai", "m1"), ("anthropic", "m2")]:
        other = project(log.events, provider=provider, model=model)
        am = other.messages[1]
        assert isinstance(am, AssistantMessage)
        assert [i.kind for i in am.items] == ["text", "tool_use"]
        # opaque만 있던 메시지는 통째로 빠진다
        assert len(other.messages) == 3
    # 원본 이벤트는 바뀌지 않는다
    assert log.events[1].as_assistant_message().items[0] == think


def test_compaction_substitutes_range_with_synthetic_summary() -> None:
    log = Log()
    log.add("user_message", UserMessage.from_text("first"))  # 1
    log.add("assistant_message", assistant(tu("a")))  # 2
    log.add("tool_started", ToolStartedPayload(tool_use_id="a", name="read", index=0))  # 3
    log.add("tool_result", ToolResult(tool_use_id="a", index=0, content="r"))  # 4
    log.add("user_message", UserMessage.from_text("second"))  # 5
    log.add("assistant_message", assistant(TextItem(text="reply")))  # 6
    log.add(
        "compaction",
        CompactionPayload(from_seq=1, to_seq=4, summary="S1", provider="other", model="x"),
    )  # 7
    log.add("user_message", UserMessage.from_text("third"))  # 8

    view = project(log.events, provider="anthropic", model="m1")
    texts = [
        m.text() if isinstance(m, UserMessage | AssistantMessage) else "group"
        for m in view.messages
    ]
    assert texts == ["S1", "second", "reply", "third"]
    first = view.messages[0]
    assert isinstance(first, UserMessage) and first.origin == "synthetic"

    # 겹치는 나중 compaction은 범위를 합치고 나중 요약을 쓴다
    log.add(
        "compaction",
        CompactionPayload(from_seq=3, to_seq=6, summary="S2", provider="anthropic", model="m1"),
    )
    view = project(log.events, provider="anthropic", model="m1")
    texts = [m.text() for m in view.messages if isinstance(m, UserMessage)]
    assert texts == ["S2", "third"]
    assert len(view.messages) == 2


def test_find_unpaired_tool_uses() -> None:
    log = Log()
    log.add("user_message", UserMessage.from_text("go"))
    log.add("assistant_message", assistant(tu("a"), tu("b"), tu("c")))
    log.add("tool_started", ToolStartedPayload(tool_use_id="a", name="read", index=0))
    log.add("tool_result", ToolResult(tool_use_id="a", index=0, content="ok"))
    log.add("tool_started", ToolStartedPayload(tool_use_id="b", name="read", index=1))

    unpaired = find_unpaired_tool_uses(log.events)
    assert [(t.id, started) for t, started in unpaired] == [("b", True), ("c", False)]

    log.add(
        "tool_result",
        ToolResult(tool_use_id="b", index=1, content="interrupted: process exited", is_error=True),
    )
    log.add(
        "tool_result", ToolResult(tool_use_id="c", index=2, content="not executed", is_error=True)
    )
    assert find_unpaired_tool_uses(log.events) == []
    group = project(log.events, provider="anthropic", model="m1").messages[2]
    assert isinstance(group, ToolResultsGroup)
    assert [r.tool_use_id for r in group.results] == ["a", "b", "c"]
