"""TurnRunner / SessionRunner: FakeProvider + 실제 도구 + LocalBackend (m1.md §6 테스트 5)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from perdix.context.builder import build_system_prompt
from perdix.context.messages import (
    AssistantMessage,
    TextBlock,
    TextItem,
    ToolResultsGroup,
    UserMessage,
)
from perdix.context.view import find_unpaired_tool_uses, project
from perdix.harness.budget import Budget
from perdix.harness.events import (
    ContextUsage,
    HarnessEvent,
    PermissionOutcome,
    PermissionRequest,
    TextDelta,
    ToolFailed,
    ToolFinished,
    ToolRunning,
    ToolStarted,
    TurnEnded,
)
from perdix.harness.hooks import HookAllow, HookBus, HookDeny, HookMutate, PreToolDecision
from perdix.harness.modes import BUILD, CHAT, Mode
from perdix.harness.runner import SessionRunner, TurnBusyError
from perdix.harness.turn import RECONNECT_TEXT, TurnResult, TurnRunner
from perdix.llm import base as llm
from perdix.llm.fake import FakeProvider
from perdix.permissions.policy import PermissionPolicy, Rule
from perdix.sandbox.local import LocalBackend
from perdix.session.store import EventStore
from perdix.tools.base import Tool, ToolContext, ToolKind, ToolOutput
from perdix.tools.registry import ToolRegistry, builtin_tools

# ---------------------------------------------------------------- 공통 준비


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.txt").write_text("old line\n")
    return root


@pytest.fixture
async def store() -> AsyncIterator[EventStore]:
    s = await EventStore.open(":memory:")
    yield s
    await s.close()


@pytest.fixture
async def session_id(store: EventStore, cwd: Path) -> str:
    rec = await store.create_session(str(cwd), "build", "fake", "fake-model")
    return rec.id


class Recorder:
    """sink와 ask_permission을 겸한다."""

    def __init__(self, outcomes: list[str] | None = None) -> None:
        self.events: list[HarnessEvent] = []
        self.requests: list[PermissionRequest] = []
        self.outcomes = list(outcomes or [])

    async def sink(self, event: HarnessEvent) -> None:
        self.events.append(event)

    async def ask(self, req: PermissionRequest) -> PermissionOutcome:
        self.requests.append(req)
        return PermissionOutcome.model_validate({"outcome": self.outcomes.pop(0)})

    def of(self, cls: type[Any]) -> list[Any]:
        return [e for e in self.events if isinstance(e, cls)]


def make_runner(
    store: EventStore,
    cwd: Path,
    provider: Any,
    *,
    tools: list[Tool] | None = None,
    rules: dict[str, Rule] | None = None,
    budget: Budget | None = None,
    hooks: HookBus | None = None,
    mode: Mode = BUILD,
    sleeps: list[float] | None = None,
    max_tokens: int = 1000,
) -> TurnRunner:
    async def fake_sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return TurnRunner(
        store=store,
        provider=provider,
        registry=ToolRegistry(tools if tools is not None else builtin_tools()),
        policy=PermissionPolicy(rules),
        sandbox=LocalBackend(),
        budget=budget or Budget(),
        hooks=hooks or HookBus(),
        mode=mode,
        cwd=cwd,
        system_prompt_builder=build_system_prompt,
        model="fake-model",
        max_tokens=max_tokens,
        sleep=fake_sleep,
    )


def prompt(text: str = "go") -> list[Any]:
    return [TextBlock(text=text)]


async def run(
    runner: TurnRunner, sid: str, rec: Recorder, cancel: asyncio.Event | None = None
) -> TurnResult:
    return await runner.run(
        sid, prompt(), cancel=cancel or asyncio.Event(), sink=rec.sink, ask_permission=rec.ask
    )


async def results(store: EventStore, sid: str) -> dict[str, Any]:
    return {
        r.tool_use_id: r
        for r in (e.as_tool_result() for e in await store.events(sid) if e.kind == "tool_result")
    }


async def assert_consistent(store: EventStore, sid: str) -> None:
    """event log가 unpaired tool_use 없이 투영된다."""
    events = await store.events(sid)
    assert find_unpaired_tool_uses(events) == []
    view = project(events, provider="fake", model="fake-model")
    for i, msg in enumerate(view.messages):
        if isinstance(msg, AssistantMessage) and msg.tool_uses():
            group = view.messages[i + 1]
            assert isinstance(group, ToolResultsGroup)
            assert [r.tool_use_id for r in group.results] == [t.id for t in msg.tool_uses()]


def tu(id: str, name: str, **input: Any) -> dict[str, Any]:
    return {"id": id, "name": name, "input": input}


# ---------------------------------------------------------------- 실행 순서


async def test_read_edit_read_is_serialized(store: EventStore, session_id: str, cwd: Path) -> None:
    provider = FakeProvider(
        [
            {
                "tool_uses": [
                    tu("r1", "read", path="a.txt"),
                    tu("e1", "edit", path="a.txt", old_string="old", new_string="new"),
                    tu("r2", "read", path="a.txt"),
                ]
            },
            {"text": "done"},
        ]
    )
    rec = Recorder()
    runner = make_runner(store, cwd, provider, rules={"edit": "allow"})
    res = await run(runner, session_id, rec)

    assert res.stop_reason == "end_turn"
    assert res.verification == "skipped"
    out = await results(store, session_id)
    assert "old line" in out["r1"].content
    assert not out["e1"].is_error
    assert "new line" in out["r2"].content
    assert [out[i].index for i in ("r1", "e1", "r2")] == [0, 1, 2]
    # 두 번째 요청의 마지막 메시지는 index 순 결과 묶음
    last = provider.requests[1].view.messages[-1]
    assert isinstance(last, ToolResultsGroup)
    assert [r.tool_use_id for r in last.results] == ["r1", "e1", "r2"]
    assert [e.tool_use_id for e in rec.of(ToolStarted)] == ["r1", "e1", "r2"]
    assert len(rec.of(ToolFinished)) == 3
    assert rec.requests == []  # edit은 정책이 allow
    await assert_consistent(store, session_id)


class BarrierProbe:
    """두 인스턴스가 동시에 실행돼야만 장벽을 통과한다."""

    description = "probe"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self, name: str, barrier: asyncio.Barrier, read_only: bool) -> None:
        self.name = name
        self.kind: ToolKind = "read" if read_only else "execute"
        self._barrier = barrier
        self._read_only = read_only

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return self._read_only

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        try:
            await asyncio.wait_for(self._barrier.wait(), timeout=0.3)
        except TimeoutError:
            return ToolOutput(content="alone")
        return ToolOutput(content="together")


async def test_read_only_segment_runs_concurrently(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    barrier = asyncio.Barrier(2)
    tools: list[Tool] = [BarrierProbe("p1", barrier, True), BarrierProbe("p2", barrier, True)]
    provider = FakeProvider([{"tool_uses": [tu("a", "p1"), tu("b", "p2")]}, {"text": "ok"}])
    rec = Recorder()
    runner = make_runner(store, cwd, provider, tools=tools, rules={"p1": "allow", "p2": "allow"})
    assert (await run(runner, session_id, rec)).stop_reason == "end_turn"
    out = await results(store, session_id)
    assert out["a"].content == "together"
    assert out["b"].content == "together"
    await assert_consistent(store, session_id)


async def test_write_tools_run_one_at_a_time(store: EventStore, session_id: str, cwd: Path) -> None:
    barrier = asyncio.Barrier(2)
    tools: list[Tool] = [BarrierProbe("w1", barrier, False), BarrierProbe("w2", barrier, False)]
    provider = FakeProvider([{"tool_uses": [tu("a", "w1"), tu("b", "w2")]}, {"text": "ok"}])
    rec = Recorder()
    runner = make_runner(store, cwd, provider, tools=tools, rules={"w1": "allow", "w2": "allow"})
    await run(runner, session_id, rec)
    out = await results(store, session_id)
    assert out["a"].content == "alone"
    assert out["b"].content == "alone"


async def test_invalid_input_and_unknown_tool_are_error_results(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    provider = FakeProvider([{"tool_uses": [tu("x", "read"), tu("y", "nope")]}, {"text": "ok"}])
    rec = Recorder()
    res = await run(make_runner(store, cwd, provider), session_id, rec)
    assert res.stop_reason == "end_turn"
    out = await results(store, session_id)
    assert out["x"].is_error and "invalid input" in out["x"].content
    assert out["y"].is_error and "unknown tool" in out["y"].content
    assert {e.tool_use_id for e in rec.of(ToolFailed)} == {"x", "y"}
    await assert_consistent(store, session_id)


async def test_read_only_mode_hides_write_tools(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    provider = FakeProvider(
        [{"tool_uses": [tu("w", "write", path="b.txt", content="x")]}, {"text": "ok"}]
    )
    rec = Recorder()
    res = await run(make_runner(store, cwd, provider, mode=CHAT), session_id, rec)
    assert res.verification is None
    assert "write" not in {t.name for t in provider.requests[0].tools}
    assert (await results(store, session_id))["w"].is_error
    assert not (cwd / "b.txt").exists()


# ---------------------------------------------------------------- 권한과 훅


async def test_permission_reject_once_returns_error_and_continues(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    provider = FakeProvider(
        [{"tool_uses": [tu("b1", "bash", command="echo hi")]}, {"text": "fine"}]
    )
    rec = Recorder(["reject_once"])
    res = await run(make_runner(store, cwd, provider), session_id, rec)
    assert res.stop_reason == "end_turn"
    assert [r.tool_use_id for r in rec.requests] == ["b1"]
    assert rec.requests[0].kind == "execute"
    out = await results(store, session_id)
    assert out["b1"].is_error and out["b1"].content == "user rejected"
    assert rec.of(ToolFailed)[0].reason == "user rejected"
    assert len(provider.requests) == 2
    kinds = [e.kind for e in await store.events(session_id)]
    assert "tool_started" not in kinds  # 실행 안 함
    assert "permission_decision" not in kinds
    await assert_consistent(store, session_id)


async def test_permission_allow_always_is_remembered(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    provider = FakeProvider(
        [
            {
                "tool_uses": [
                    tu("w1", "write", path="b.txt", content="1"),
                    tu("w2", "write", path="c.txt", content="2"),
                ]
            },
            {"tool_uses": [tu("w3", "write", path="d.txt", content="3")]},
            {"text": "ok"},
        ]
    )
    rec = Recorder(["allow_always"])
    runner = make_runner(store, cwd, provider)
    res = await run(runner, session_id, rec)
    assert res.stop_reason == "end_turn"
    assert [r.tool_use_id for r in rec.requests] == ["w1"]  # 뒤의 요청은 재평가로 통과
    assert runner.policy.session_rules == {"write": "allow"}
    assert (cwd / "b.txt").read_text() == "1"
    assert (cwd / "d.txt").read_text() == "3"
    decisions = [
        e.as_permission_decision()
        for e in await store.events(session_id)
        if e.kind == "permission_decision"
    ]
    assert [(d.tool_use_id, d.decision) for d in decisions] == [("w1", "allow_always")]


async def test_policy_deny_and_hooks(store: EventStore, session_id: str, cwd: Path) -> None:
    hooks = HookBus()

    async def pre(name: str, input: dict[str, Any]) -> PreToolDecision:
        if input.get("path") == "secret.txt":
            return HookDeny(reason="blocked by hook")
        if input.get("path") == "redirect.txt":
            return HookMutate(input={"path": "a.txt"})
        return HookMutate(input=input)

    async def post(name: str, input: dict[str, Any], output: ToolOutput) -> ToolOutput:
        return output.model_copy(update={"content": "[post] " + output.content})

    hooks.pre_tool.append(pre)
    hooks.post_tool.append(post)
    provider = FakeProvider(
        [
            {
                "tool_uses": [
                    tu("d", "bash", command="rm -rf /"),
                    tu("s", "read", path="secret.txt"),
                    tu("r", "read", path="redirect.txt"),
                ]
            },
            {"text": "ok"},
        ]
    )
    rec = Recorder()
    runner = make_runner(store, cwd, provider, rules={"bash": "deny"}, hooks=hooks)
    await run(runner, session_id, rec)
    out = await results(store, session_id)
    assert out["d"].content == "denied by policy"
    assert out["s"].is_error and out["s"].content == "blocked by hook"
    assert out["r"].content.startswith("[post] ") and "old line" in out["r"].content
    await assert_consistent(store, session_id)


# ---------------------------------------------------------------- RECOVER 분기


async def test_max_tokens_text_injects_synthetic_continue_once(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    provider = FakeProvider(
        [{"text": "part", "stop": "max_tokens"}, {"text": "rest", "stop": "max_tokens"}]
    )
    rec = Recorder()
    res = await run(make_runner(store, cwd, provider), session_id, rec)
    assert res.stop_reason == "max_tokens"
    assert len(provider.requests) == 2
    last = provider.requests[1].view.messages[-1]
    assert isinstance(last, UserMessage)
    assert last.origin == "synthetic" and last.text() == "계속"
    assistant = [e for e in await store.events(session_id) if e.kind == "assistant_message"]
    assert [e.as_assistant_message().text() for e in assistant] == ["part", "rest"]


async def test_max_tokens_text_then_finish(store: EventStore, session_id: str, cwd: Path) -> None:
    provider = FakeProvider([{"text": "part", "stop": "max_tokens"}, {"text": "rest"}])
    res = await run(make_runner(store, cwd, provider), session_id, Recorder())
    assert res.stop_reason == "end_turn"


async def test_truncated_tool_use_retries_with_doubled_max_tokens(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    truncated = {
        "tool_uses": [tu("ok1", "read", path="a.txt"), tu("cut", "read", path="a.txt")],
        "truncated_tool_use": True,
    }
    provider = FakeProvider([truncated, truncated])
    rec = Recorder()
    res = await run(make_runner(store, cwd, provider, max_tokens=1000), session_id, rec)
    assert res.stop_reason == "max_tokens"
    assert [r.max_tokens for r in provider.requests] == [1000, 2000]
    kinds = [e.kind for e in await store.events(session_id)]
    assert kinds == ["user_message"]  # 깨진 assistant 메시지는 커밋하지 않는다
    # 보낸 tool_call(완성된 ok1)은 매 시도마다 failed로 닫는다
    assert [e.tool_use_id for e in rec.of(ToolStarted)] == ["ok1", "ok1"]
    assert [e.tool_use_id for e in rec.of(ToolFailed)] == ["ok1", "ok1"]
    await assert_consistent(store, session_id)


async def test_truncated_tool_use_retry_succeeds(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    provider = FakeProvider(
        [
            {"tool_uses": [tu("cut", "read", path="a.txt")], "truncated_tool_use": True},
            {"text": "short answer"},
        ]
    )
    res = await run(make_runner(store, cwd, provider, max_tokens=100_000), session_id, Recorder())
    assert res.stop_reason == "end_turn"
    assert [r.max_tokens for r in provider.requests] == [100_000, 128_000]


async def test_retry_before_first_chunk(store: EventStore, session_id: str, cwd: Path) -> None:
    err = {"error": {"retryable": True, "status": 529}}
    provider = FakeProvider([err, err, {"text": "ok"}])
    sleeps: list[float] = []
    rec = Recorder()
    res = await run(make_runner(store, cwd, provider, sleeps=sleeps), session_id, rec)
    assert res.stop_reason == "end_turn"
    assert sleeps == [0.5, 1.0]
    assert [e.text for e in rec.of(TextDelta)] == ["ok"]


async def test_retry_exhausted_and_non_retryable_raise(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    err = {"error": {"retryable": True, "status": 500}}
    sleeps: list[float] = []
    runner = make_runner(store, cwd, FakeProvider([err] * 4), sleeps=sleeps)
    with pytest.raises(llm.ProviderError):
        await run(runner, session_id, Recorder())
    assert sleeps == [0.5, 1.0, 2.0]

    fatal = {"error": {"retryable": False, "status": 400}}
    with pytest.raises(llm.ProviderError):
        await run(make_runner(store, cwd, FakeProvider([fatal])), session_id, Recorder())


async def test_error_after_first_chunk_reconnects_without_commit(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    provider = FakeProvider(
        [
            {"text": "partial", "error": {"retryable": True, "after_first_chunk": True}},
            {"text": "final"},
        ]
    )
    sleeps: list[float] = []
    rec = Recorder()
    res = await run(make_runner(store, cwd, provider, sleeps=sleeps), session_id, rec)
    assert res.stop_reason == "end_turn"
    assert [e.text for e in rec.of(TextDelta)] == ["partial", RECONNECT_TEXT, "final"]
    texts = [
        e.as_assistant_message().text()
        for e in await store.events(session_id)
        if e.kind == "assistant_message"
    ]
    assert texts == ["final"]
    assert sleeps == []


class DropAfterToolProvider:
    """첫 호출은 tool_use 하나를 보낸 뒤 연결이 끊기고, 두 번째는 정상 종료."""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def sanitize_tool_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        return schema

    async def stream(self, req: llm.ModelRequest) -> AsyncGenerator[llm.ModelEvent, None]:
        self.calls += 1
        if self.calls == 1:
            yield llm.ToolUseStart(id="t1", name="read")
            yield llm.ToolUseEnd(id="t1", input={"path": "a.txt"})
            raise llm.ProviderError("reset", retryable=True)
        yield llm.TextDelta(text="ok")
        yield llm.Usage(input_tokens=42)
        msg = AssistantMessage(
            items=[TextItem(text="ok")], provider="fake", model="fake-model", stop_reason="end_turn"
        )
        yield llm.Stop(reason="end_turn", message=msg)


async def test_error_after_first_chunk_closes_sent_tool_calls(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    rec = Recorder()
    res = await run(make_runner(store, cwd, DropAfterToolProvider()), session_id, rec)
    assert res.stop_reason == "end_turn"
    assert [e.tool_use_id for e in rec.of(ToolFailed)] == ["t1"]
    types = [e.type for e in rec.events]
    assert types.index("tool_failed") < types.index("text_delta")
    assert rec.of(TextDelta)[0].text == RECONNECT_TEXT
    assert rec.of(ContextUsage)[-1] == ContextUsage(used=42, size=200_000)
    assert "tool_started" not in [e.kind for e in await store.events(session_id)]
    await assert_consistent(store, session_id)


async def test_refusal(store: EventStore, session_id: str, cwd: Path) -> None:
    provider = FakeProvider([{"text": "I can't help with that", "stop": "refusal"}])
    rec = Recorder()
    res = await run(make_runner(store, cwd, provider), session_id, rec)
    assert res.stop_reason == "refusal"
    assert rec.events[-1] == TurnEnded(stop_reason="refusal", verification=None)


async def test_step_budget(store: EventStore, session_id: str, cwd: Path) -> None:
    step = {"tool_uses": [tu("r", "read", path="a.txt")]}
    provider = FakeProvider(
        [
            {"tool_uses": [tu("r1", "read", path="a.txt")]},
            {"tool_uses": [tu("r2", "read", path="a.txt")]},
            step,
        ]
    )
    rec = Recorder()
    res = await run(make_runner(store, cwd, provider, budget=Budget(max_steps=2)), session_id, rec)
    assert res.stop_reason == "max_turn_requests"
    assert len(provider.requests) == 2
    usage = rec.of(ContextUsage)
    assert len(usage) == 2 and usage[0] == ContextUsage(used=10, size=200_000)
    await assert_consistent(store, session_id)


# ---------------------------------------------------------------- 취소


async def test_cancel_mid_tool(store: EventStore, session_id: str, cwd: Path) -> None:
    provider = FakeProvider(
        [
            {
                "tool_uses": [
                    tu("slow", "bash", command="sleep 30"),
                    tu("after", "read", path="a.txt"),
                ]
            }
        ]
    )
    rec = Recorder()
    cancel = asyncio.Event()
    runner = make_runner(store, cwd, provider, rules={"bash": "allow"})
    task = asyncio.create_task(run(runner, session_id, rec, cancel))
    for _ in range(200):
        if rec.of(ToolRunning):
            break
        await asyncio.sleep(0.01)
    assert [e.tool_use_id for e in rec.of(ToolRunning)] == ["slow"]
    cancel.set()
    res = await asyncio.wait_for(task, timeout=10)
    assert res.stop_reason == "cancelled"
    out = await results(store, session_id)
    assert out["slow"].is_error and out["slow"].content == "cancelled"
    assert out["after"].is_error and out["after"].content == "cancelled"
    kinds = [e.kind for e in await store.events(session_id)]
    assert kinds.count("tool_started") == 1  # after는 시작도 안 함
    assert {e.tool_use_id for e in rec.of(ToolFailed)} == {"slow", "after"}
    assert rec.events[-1] == TurnEnded(stop_reason="cancelled")
    assert len(provider.requests) == 1
    await assert_consistent(store, session_id)


async def test_cancel_during_stream(store: EventStore, session_id: str, cwd: Path) -> None:
    gate = asyncio.Event()

    class HangingProvider:
        name = "fake"

        def sanitize_tool_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
            return schema

        async def stream(self, req: llm.ModelRequest) -> AsyncGenerator[llm.ModelEvent, None]:
            yield llm.TextDelta(text="done text")
            yield llm.ToolUseStart(id="t", name="read")
            gate.set()
            await asyncio.sleep(30)
            yield llm.TextDelta(text="never")

    rec = Recorder()
    cancel = asyncio.Event()
    runner = make_runner(store, cwd, HangingProvider())
    task = asyncio.create_task(run(runner, session_id, rec, cancel))
    await gate.wait()
    cancel.set()
    res = await asyncio.wait_for(task, timeout=5)
    assert res.stop_reason == "cancelled"
    msgs = [
        e.as_assistant_message()
        for e in await store.events(session_id)
        if e.kind == "assistant_message"
    ]
    assert [(m.text(), m.tool_uses()) for m in msgs] == [("done text", [])]
    await assert_consistent(store, session_id)


# ---------------------------------------------------------------- SessionRunner


async def test_session_runner_rejects_concurrent_prompt_and_cancels(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    provider = FakeProvider([{"tool_uses": [tu("b", "bash", command="echo hi")]}])
    asked = asyncio.Event()
    release = asyncio.Event()
    rec = Recorder()

    async def slow_ask(req: PermissionRequest) -> PermissionOutcome:
        asked.set()
        await release.wait()
        return PermissionOutcome(outcome="cancelled")

    sessions = SessionRunner(lambda sid: make_runner(store, cwd, provider))
    first = asyncio.create_task(
        sessions.prompt(session_id, prompt(), sink=rec.sink, ask_permission=slow_ask)
    )
    await asked.wait()
    assert sessions.is_running(session_id)
    with pytest.raises(TurnBusyError):
        await sessions.prompt(session_id, prompt(), sink=rec.sink, ask_permission=slow_ask)
    sessions.cancel(session_id)
    release.set()
    res = await first
    assert res.stop_reason == "cancelled"
    assert not sessions.is_running(session_id)
    out = await results(store, session_id)
    assert out["b"].content == "cancelled"
    await assert_consistent(store, session_id)


async def test_session_runner_sequential_prompts(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    provider = FakeProvider([{"text": "one"}, {"text": "two"}])
    sessions = SessionRunner(lambda sid: make_runner(store, cwd, provider))
    rec = Recorder()
    for _ in range(2):
        res = await sessions.prompt(session_id, prompt(), sink=rec.sink, ask_permission=rec.ask)
        assert res.stop_reason == "end_turn"
    # 두 번째 요청은 첫 Turn 히스토리를 포함한다
    assert len(provider.requests[1].view.messages) == 3


async def test_model_switch_drops_opaque_thinking_from_next_request(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    """같은 세션에서 모델을 바꾸면 이전 모델의 thinking 블록은 다음 요청의 투영에서 빠진다."""
    from perdix.context.messages import OpaqueItem

    thinking_raw = {"type": "thinking", "thinking": "x", "signature": "s"}
    first = FakeProvider(
        [{"text": "think-a", "thinking_raw": thinking_raw, "stop": "end_turn"}],
        model="model-a",
    )
    runner_a = make_runner(store, cwd, first, mode=CHAT)
    runner_a.model = "model-a"
    assert (await run(runner_a, session_id, Recorder())).stop_reason == "end_turn"
    events = await store.events(session_id)
    assistant = [e.as_assistant_message() for e in events if e.kind == "assistant_message"]
    assert any(isinstance(i, OpaqueItem) for i in assistant[-1].items)

    # 같은 모델로 이어가면 thinking 블록이 그대로 전달된다.
    same = FakeProvider([{"text": "again", "stop": "end_turn"}], model="model-a")
    runner_same = make_runner(store, cwd, same, mode=CHAT)
    runner_same.model = "model-a"
    await run(runner_same, session_id, Recorder())
    view_same = same.requests[0].view
    assert any(
        isinstance(i, OpaqueItem)
        for m in view_same.messages if isinstance(m, AssistantMessage) for i in m.items
    )

    # 모델 전환(set_config_option과 동일한 효과): 다음 요청의 투영에 OpaqueItem이 없어야 한다.
    switched = FakeProvider([{"text": "b", "stop": "end_turn"}], model="model-b")
    runner_b = make_runner(store, cwd, switched, mode=CHAT)
    runner_b.model = "model-b"
    await run(runner_b, session_id, Recorder())
    view_b = switched.requests[0].view
    assert not any(
        isinstance(i, OpaqueItem)
        for m in view_b.messages if isinstance(m, AssistantMessage) for i in m.items
    )
    # 텍스트 항목은 유지되고 원본 이벤트는 그대로 남는다.
    assert any(
        m.text() == "think-a" for m in view_b.messages if isinstance(m, AssistantMessage)
    )
    assert len([e for e in await store.events(session_id) if e.kind == "assistant_message"]) == 3


def test_system_prompt_is_stable(cwd: Path) -> None:
    a = build_system_prompt(cwd, BUILD)
    assert a == build_system_prompt(cwd, BUILD)
    assert str(cwd) in a and "build" in a
    assert "read-only" in build_system_prompt(cwd, CHAT)


# ---------------------------------------------------------------- 리뷰 지적 회귀 테스트


class RaisingTool:
    """run()이 예외를 던지는 쓰기 도구."""

    name = "boom"
    description = "raises"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    kind: ToolKind = "execute"

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return False

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        raise RuntimeError("kaboom")


async def test_write_tool_exception_becomes_error_result(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    provider = FakeProvider([{"tool_uses": [tu("x", "boom")]}, {"text": "ok"}])
    rec = Recorder()
    runner = make_runner(store, cwd, provider, tools=[RaisingTool()], rules={"boom": "allow"})
    res = await run(runner, session_id, rec)
    assert res.stop_reason == "end_turn"
    out = await results(store, session_id)
    assert out["x"].is_error and "kaboom" in out["x"].content
    await assert_consistent(store, session_id)


async def test_write_path_hook_exception_becomes_error_result(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    """pre_tool 훅 예외처럼 `_execute` 바깥으로 새는 예외도 결과로 기록한다."""
    hooks = HookBus()

    async def pre(name: str, input: dict[str, Any]) -> PreToolDecision:
        raise RuntimeError("hook exploded")

    hooks.pre_tool.append(pre)
    provider = FakeProvider(
        [{"tool_uses": [tu("w", "write", path="b.txt", content="x")]}, {"text": "ok"}]
    )
    rec = Recorder()
    runner = make_runner(store, cwd, provider, rules={"write": "allow"}, hooks=hooks)
    res = await run(runner, session_id, rec)
    assert res.stop_reason == "end_turn"
    out = await results(store, session_id)
    assert out["w"].is_error and "hook exploded" in out["w"].content
    assert not (cwd / "b.txt").exists()
    await assert_consistent(store, session_id)


async def test_turn_start_repairs_unpaired_tool_uses(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    """이전 Turn이 결과 없이 끝나도 다음 Turn이 §3.4 복구 후 정상 진행한다."""
    from perdix.context.messages import ToolUseItem
    from perdix.session.recovery import INTERRUPTED_TEXT, NOT_EXECUTED_TEXT
    from perdix.session.store import ToolStartedPayload

    await store.append(session_id, "user_message", UserMessage.from_text("earlier"))
    await store.append(
        session_id,
        "assistant_message",
        AssistantMessage(
            items=[
                ToolUseItem(id="s1", name="read", input={"path": "a.txt"}),
                ToolUseItem(id="n1", name="read", input={"path": "a.txt"}),
            ],
            provider="fake",
            model="fake-model",
            stop_reason="tool_use",
        ),
    )
    await store.append(
        session_id, "tool_started", ToolStartedPayload(tool_use_id="s1", name="read", index=0)
    )
    provider = FakeProvider([{"text": "ok"}])
    res = await run(make_runner(store, cwd, provider), session_id, Recorder())
    assert res.stop_reason == "end_turn"
    out = await results(store, session_id)
    assert out["s1"].is_error and out["s1"].content == INTERRUPTED_TEXT
    assert out["n1"].is_error and out["n1"].content == NOT_EXECUTED_TEXT
    assert [out["s1"].index, out["n1"].index] == [0, 1]
    group = provider.requests[0].view.messages[2]
    assert isinstance(group, ToolResultsGroup)
    assert [r.tool_use_id for r in group.results] == ["s1", "n1"]
    await assert_consistent(store, session_id)


async def test_session_runner_guard_and_prompt_exclude_each_other(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    provider = FakeProvider([{"tool_uses": [tu("b", "bash", command="echo hi")]}, {"text": "x"}])
    sessions = SessionRunner(lambda sid: make_runner(store, cwd, provider))
    rec = Recorder()

    # load(guard)가 잡고 있으면 prompt는 거절
    async with sessions.guard(session_id):
        assert sessions.is_running(session_id)
        with pytest.raises(TurnBusyError):
            await sessions.prompt(session_id, prompt(), sink=rec.sink, ask_permission=rec.ask)
    assert not sessions.is_running(session_id)

    # prompt가 잡고 있으면 guard는 거절
    asked = asyncio.Event()
    release = asyncio.Event()

    async def slow_ask(req: PermissionRequest) -> PermissionOutcome:
        asked.set()
        await release.wait()
        return PermissionOutcome(outcome="reject_once")

    first = asyncio.create_task(
        sessions.prompt(session_id, prompt(), sink=rec.sink, ask_permission=slow_ask)
    )
    await asked.wait()
    with pytest.raises(TurnBusyError):
        async with sessions.guard(session_id):
            pytest.fail("guard must not be acquired during a turn")
    release.set()
    assert (await first).stop_reason == "end_turn"
    async with sessions.guard(session_id):
        pass


async def test_cancel_unblocks_pending_permission(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    provider = FakeProvider(
        [{"tool_uses": [tu("b", "bash", command="echo hi"), tu("r", "read", path="a.txt")]}]
    )
    asked = asyncio.Event()
    ask_cancelled = asyncio.Event()

    async def never(req: PermissionRequest) -> PermissionOutcome:
        asked.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            ask_cancelled.set()
            raise
        raise AssertionError("unreachable")

    rec = Recorder()
    cancel = asyncio.Event()
    runner = make_runner(store, cwd, provider)
    task = asyncio.create_task(
        runner.run(session_id, prompt(), cancel=cancel, sink=rec.sink, ask_permission=never)
    )
    await asyncio.wait_for(asked.wait(), timeout=5)
    cancel.set()
    res = await asyncio.wait_for(task, timeout=5)
    assert res.stop_reason == "cancelled"
    assert ask_cancelled.is_set()
    out = await results(store, session_id)
    assert out["b"].content == "cancelled"
    assert out["r"].content == "cancelled"
    assert rec.events[-1] == TurnEnded(stop_reason="cancelled")
    await assert_consistent(store, session_id)


async def test_truncated_retry_capped_at_model_max_output(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    truncated = {"tool_uses": [tu("cut", "read", path="a.txt")], "truncated_tool_use": True}
    provider = FakeProvider([truncated, {"text": "short"}])
    runner = make_runner(store, cwd, provider, max_tokens=40_000)
    runner.model = "claude-haiku-4-5"  # max_output 64K
    res = await run(runner, session_id, Recorder())
    assert res.stop_reason == "end_turn"
    assert [r.max_tokens for r in provider.requests] == [40_000, 64_000]

    # 이미 상한이면 재요청하지 않는다
    provider2 = FakeProvider([truncated])
    runner2 = make_runner(store, cwd, provider2, max_tokens=64_000)
    runner2.model = "claude-haiku-4-5"
    assert (await run(runner2, session_id, Recorder())).stop_reason == "max_tokens"
    assert [r.max_tokens for r in provider2.requests] == [64_000]


async def test_hook_mutated_input_is_reevaluated_by_policy(
    store: EventStore, session_id: str, cwd: Path
) -> None:
    hooks = HookBus()

    async def pre(name: str, input: dict[str, Any]) -> PreToolDecision:
        if input.get("command") == "echo deny":
            return HookMutate(input={"command": "rm -rf x"})
        if input.get("command") == "echo ask":
            return HookMutate(input={"command": "make build"})
        return HookAllow()

    hooks.pre_tool.append(pre)
    provider = FakeProvider(
        [
            {
                "tool_uses": [
                    tu("d", "bash", command="echo deny"),
                    tu("a", "bash", command="echo ask"),
                    tu("k", "bash", command="echo ok"),
                ]
            },
            {"text": "done"},
        ]
    )
    rec = Recorder(["reject_once"])
    rules: dict[str, Rule] = {"bash": {"echo *": "allow", "rm *": "deny", "*": "ask"}}
    runner = make_runner(store, cwd, provider, rules=rules, hooks=hooks)
    res = await run(runner, session_id, rec)
    assert res.stop_reason == "end_turn"
    out = await results(store, session_id)
    assert out["d"].is_error and out["d"].content == "denied by policy"
    assert out["a"].is_error and out["a"].content == "user rejected"
    assert not out["k"].is_error and out["k"].content == "ok"
    # 원래 입력은 allow였고, 바뀐 입력에 대해서만 물었다
    assert [(r.tool_use_id, r.input) for r in rec.requests] == [
        ("a", {"command": "make build"})
    ]
    await assert_consistent(store, session_id)
