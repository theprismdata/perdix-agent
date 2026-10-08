import asyncio
import sqlite3
from pathlib import Path

import pytest

from perdix.context.messages import (
    AssistantMessage,
    TextItem,
    ToolResult,
    ToolUseItem,
    UserMessage,
)
from perdix.session.store import (
    SCHEMA_VERSION,
    CompactionPayload,
    EventStore,
    SchemaTooNewError,
    ToolStartedPayload,
)


@pytest.fixture
async def store(tmp_path: Path):  # type: ignore[no-untyped-def]
    s = await EventStore.open(tmp_path / "perdix.db")
    yield s
    await s.close()


async def test_open_sets_wal_and_schema_version(tmp_path: Path) -> None:
    db = tmp_path / "perdix.db"
    s = await EventStore.open(db)
    await s.close()
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert int(row[0]) == SCHEMA_VERSION
    conn.close()


async def test_memory_db_works() -> None:
    s = await EventStore.open(":memory:")
    sess = await s.create_session("/p", "build", "fake", "m")
    assert await s.append(sess.id, "user_message", UserMessage.from_text("hi")) == 1
    await s.close()


async def test_append_seq_and_events_roundtrip(store: EventStore) -> None:
    sess = await store.create_session("/proj", "build", "anthropic", "claude")
    other = await store.create_session("/proj", "build", "anthropic", "claude")
    assert await store.append(sess.id, "user_message", UserMessage.from_text("hello")) == 1
    am = AssistantMessage(
        items=[TextItem(text="ok"), ToolUseItem(id="t1", name="read", input={"path": "a"})],
        provider="anthropic",
        model="claude",
        stop_reason="tool_use",
    )
    assert await store.append(sess.id, "assistant_message", am) == 2
    started = ToolStartedPayload(tool_use_id="t1", name="read", index=0)
    assert await store.append(sess.id, "tool_started", started) == 3
    result = ToolResult(tool_use_id="t1", index=0, content="data")
    assert await store.append(sess.id, "tool_result", result) == 4
    assert await store.append(sess.id, "mode_change", {"mode": "plan"}) == 5
    # 세션마다 seq는 독립
    assert await store.append(other.id, "user_message", UserMessage.from_text("x")) == 1

    evs = await store.events(sess.id)
    assert [e.seq for e in evs] == [1, 2, 3, 4, 5]
    assert [e.kind for e in evs] == [
        "user_message",
        "assistant_message",
        "tool_started",
        "tool_result",
        "mode_change",
    ]
    assert evs[0].as_user_message().text() == "hello"
    assert evs[1].as_assistant_message() == am
    assert evs[2].as_tool_started() == started
    assert evs[3].as_tool_result() == result
    assert evs[4].payload == {"mode": "plan"}
    with pytest.raises(ValueError):
        evs[0].as_tool_result()


async def test_append_rejects_unknown_kind_and_bad_payload(store: EventStore) -> None:
    sess = await store.create_session("/proj", "build", "fake", "m")
    with pytest.raises(ValueError):
        await store.append(sess.id, "bogus", {})
    with pytest.raises(ValueError):
        await store.append(sess.id, "tool_result", {"tool_use_id": "x"})
    with pytest.raises(TypeError):
        await store.append(sess.id, "tool_result", UserMessage.from_text("x"))
    assert await store.events(sess.id) == []


async def test_concurrent_appends_gapless_and_unique(tmp_path: Path) -> None:
    db = tmp_path / "perdix.db"
    a = await EventStore.open(db)
    b = await EventStore.open(db)
    try:
        sess = await a.create_session("/proj", "build", "fake", "m")
        other = await a.create_session("/proj", "build", "fake", "m")

        async def put(s: EventStore, sid: str, i: int) -> int:
            return await s.append(sid, "user_message", UserMessage.from_text(f"m{i}"))

        seqs = await asyncio.gather(
            *(put(a if i % 2 else b, sess.id, i) for i in range(60)),
            *(put(b, other.id, i) for i in range(10)),
        )
        assert sorted(seqs[:60]) == list(range(1, 61))
        assert sorted(seqs[60:]) == list(range(1, 11))
        assert [e.seq for e in await a.events(sess.id)] == list(range(1, 61))
        assert [e.seq for e in await b.events(other.id)] == list(range(1, 11))
    finally:
        await a.close()
        await b.close()


async def test_sessions_get_list_update(store: EventStore) -> None:
    s1 = await store.create_session("/a", "build", "fake", "m")
    s2 = await store.create_session("/b", "plan", "fake", "m")
    assert await store.get_session("missing") is None
    got = await store.get_session(s1.id)
    assert got is not None and got.project_path == "/a" and got.permission_rules == []

    await store.update_session(
        s1.id, title="T", mode="plan", permission_rules=[{"tool": "bash", "action": "allow"}]
    )
    got = await store.get_session(s1.id)
    assert got is not None
    assert got.title == "T" and got.mode == "plan"
    assert got.permission_rules == [{"tool": "bash", "action": "allow"}]
    assert got.updated_at >= s1.updated_at
    with pytest.raises(ValueError):
        await store.update_session(s1.id, project_path="/x")

    all_ids = [s.id for s in await store.list_sessions()]
    assert all_ids[0] == s1.id  # 가장 최근 갱신
    assert set(all_ids) == {s1.id, s2.id}
    assert [s.id for s in await store.list_sessions("/b")] == [s2.id]


async def test_fts_search_scope_and_ordering(store: EventStore) -> None:
    here = await store.create_session("/here", "build", "fake", "m")
    there = await store.create_session("/there", "build", "fake", "m")
    await store.append(there.id, "user_message", UserMessage.from_text("refactor the parser"))
    await store.append(here.id, "user_message", UserMessage.from_text("parser bug in lexer"))
    await store.append(
        here.id,
        "assistant_message",
        AssistantMessage(
            items=[TextItem(text="nothing relevant")],
            provider="fake",
            model="m",
            stop_reason="end_turn",
        ),
    )
    await store.append(
        here.id, "tool_result", ToolResult(tool_use_id="t", index=0, content="parser")
    )
    await store.append(
        there.id,
        "compaction",
        CompactionPayload(from_seq=1, to_seq=1, summary="parser summary", provider="f", model="m"),
    )

    hits = await store.search("parser", "all", "/here")
    assert hits[0].session_id == here.id and hits[0].same_project
    assert {h.kind for h in hits} == {"user_message", "compaction"}  # tool_result 미색인
    assert [h.same_project for h in hits] == [True, False, False]
    assert all(h.project_path in ("/here", "/there") for h in hits)

    proj = await store.search("parser", "project", "/here")
    assert [(h.session_id, h.seq) for h in proj] == [(here.id, 1)]
    assert proj[0].text == "parser bug in lexer"

    assert len(await store.search("parser", "all", None)) == 3
    assert await store.search('"weird -syntax(', "all", None) == []
    assert await store.search("   ", "all", None) == []
    assert len(await store.search("parser", "all", None, limit=1)) == 1
    with pytest.raises(ValueError):
        await store.search("parser", "project", None)


async def test_newer_schema_version_refuses_writes(tmp_path: Path) -> None:
    db = tmp_path / "perdix.db"
    s = await EventStore.open(db)
    sess = await s.create_session("/p", "build", "fake", "m")
    await s.append(sess.id, "user_message", UserMessage.from_text("old"))
    await s.close()

    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION + 1),)
    )
    conn.commit()
    conn.close()

    s = await EventStore.open(db)
    try:
        assert s.read_only
        # 읽기는 허용
        assert len(await s.events(sess.id)) == 1
        assert (await s.get_session(sess.id)) is not None
        with pytest.raises(SchemaTooNewError):
            await s.append(sess.id, "user_message", UserMessage.from_text("new"))
        with pytest.raises(SchemaTooNewError):
            await s.create_session("/p", "build", "fake", "m")
        with pytest.raises(SchemaTooNewError):
            await s.update_session(sess.id, title="x")
    finally:
        await s.close()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT value FROM meta").fetchone()[0] == str(SCHEMA_VERSION + 1)
    conn.close()
