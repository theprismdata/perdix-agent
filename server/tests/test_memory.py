import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from filelock import FileLock

from perdix.context.builder import build_system_prompt
from perdix.context.messages import TextBlock, UserMessage
from perdix.harness.modes import BUILD
from perdix.memory.store import MAX_MEMORY_CHARS, PROMPT_MEMORY_CHARS, MemoryStore
from perdix.permissions.policy import PermissionPolicy
from perdix.sandbox.local import LocalBackend
from perdix.session.store import EventStore
from perdix.tools.base import ToolContext, validate_input
from perdix.tools.memory import MemoryAppendTool, MemoryReadTool
from perdix.tools.session_search import SessionSearchTool


@pytest.fixture
def memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    project = tmp_path / "project"
    project.mkdir()
    return MemoryStore(project)


@pytest.fixture
async def store() -> AsyncIterator[EventStore]:
    opened = await EventStore.open(":memory:")
    yield opened
    await opened.close()


def test_project_memory_persists_without_replacing_records(memory: MemoryStore) -> None:
    memory.append("project", "first-decision")
    memory.append("project", "second-decision")
    text = MemoryStore(memory.cwd).read("project")
    assert text.index("first-decision") < text.index("second-decision")


def test_global_memory_is_shared_but_project_memory_is_isolated(memory: MemoryStore) -> None:
    memory.append("global", "global-preference")
    memory.append("project", "project-decision")
    other = MemoryStore(memory.cwd.parent / "other")
    assert "global-preference" in other.read("global")
    assert other.read("project") == ""


def test_prompt_refreshes_saved_data_and_bounds_context(memory: MemoryStore) -> None:
    assert "memory-unique-value" not in build_system_prompt(memory.cwd, BUILD)
    memory.append("project", "x" * (PROMPT_MEMORY_CHARS + 10) + "memory-unique-value")
    prompt = build_system_prompt(memory.cwd, BUILD)
    assert "memory-unique-value" in prompt
    assert "x" * (PROMPT_MEMORY_CHARS + 1) not in prompt
    assert str(memory.path("project")) in prompt


async def test_locked_append_returns_error_without_changing_memory(memory: MemoryStore) -> None:
    path = memory.append("project", "original")
    ctx = ToolContext(cwd=memory.cwd, sandbox=LocalBackend())
    with FileLock(str(path) + ".lock"):
        output = await MemoryAppendTool().run(ctx, {"content": "blocked"})
    assert output.is_error
    assert "blocked" not in memory.read("project")


async def test_capacity_failure_preserves_existing_records(memory: MemoryStore) -> None:
    path = memory.path("project")
    path.parent.mkdir()
    path.write_text("x" * MAX_MEMORY_CHARS, encoding="utf-8")
    ctx = ToolContext(cwd=memory.cwd, sandbox=LocalBackend())
    output = await MemoryAppendTool().run(ctx, {"content": "overflow"})
    assert output.is_error
    assert memory.read("project") == "x" * MAX_MEMORY_CHARS


async def test_escaped_memory_path_returns_error(memory: MemoryStore, tmp_path: Path) -> None:
    (memory.cwd / ".perdix").symlink_to(tmp_path, target_is_directory=True)
    output = await MemoryReadTool().run(
        ToolContext(cwd=memory.cwd, sandbox=LocalBackend()), {}
    )
    assert output.is_error


@pytest.mark.parametrize("input", [
    {"content": " "}, {"content": "x" * 4001}, {"content": "ok", "scope": "other"},
    {"content": "ok", "path": "../escape"},
])
def test_append_schema_rejects_invalid_inputs(input: dict[str, str]) -> None:
    assert validate_input(MemoryAppendTool(), input)


def test_read_only_policy_allows_lookup_and_denies_append() -> None:
    policy = PermissionPolicy(mode="deny")
    assert policy.evaluate("memory_read", {}) == "allow"
    assert policy.evaluate("session_search", {}) == "allow"
    assert policy.evaluate("memory_append", {"content": "fact"}) == "deny"
    assert PermissionPolicy().evaluate("memory_append", {"content": "fact"}) == "ask"


async def test_session_search_returns_cross_session_sources(
    memory: MemoryStore, store: EventStore,
) -> None:
    same = await store.create_session(str(memory.cwd), "build", "fake", "fake")
    other = await store.create_session(str(memory.cwd.parent / "other"), "build", "fake", "fake")
    for session in (same, other):
        await store.append(session.id, "user_message", UserMessage(
            content=[TextBlock(text="shared-search-token")]
        ))
    ctx = ToolContext(cwd=memory.cwd, sandbox=LocalBackend(), session_store=store)
    output = await SessionSearchTool().run(ctx, {"query": "shared-search-token"})
    hits = json.loads(output.content)
    assert not output.is_error
    assert [hit["session_id"] for hit in hits] == [same.id, other.id]
    assert hits[0]["project_path"] == str(memory.cwd)
    assert hits[0]["created_at"] > 0


async def test_project_search_excludes_other_projects(
    memory: MemoryStore, store: EventStore,
) -> None:
    other = await store.create_session(str(memory.cwd.parent / "other"), "build", "fake", "fake")
    await store.append(other.id, "user_message", UserMessage(content=[TextBlock(text="foreign")]))
    output = await SessionSearchTool().run(
        ToolContext(cwd=memory.cwd, sandbox=LocalBackend(), session_store=store),
        {"query": "foreign", "scope": "project"},
    )
    assert json.loads(output.content) == []
