import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from perdix.acp.memories import SessionMemories
from perdix.context.builder import build_system_prompt
from perdix.context.messages import AssistantMessage, TextBlock, TextItem, UserMessage
from perdix.harness.modes import BUILD
from perdix.llm.base import ModelEvent, ModelRequest, Stop
from perdix.llm.fake import FakeProvider
from perdix.memory.archive import MemoryArchive
from perdix.memory.schema import MemoryConfig, MemorySnapshot
from perdix.memory.worker import MemoryWorker
from perdix.session.store import EventStore, SessionRecord


@pytest.fixture
async def store() -> AsyncIterator[EventStore]:
    opened = await EventStore.open(":memory:")
    yield opened
    await opened.close()


@pytest.fixture
def config(tmp_path: Path) -> MemoryConfig:
    return MemoryConfig(root=tmp_path / "memories", min_idle_hours=0)


async def conversation(store: EventStore, project: Path) -> SessionRecord:
    record = await store.create_session(str(project), "chat", "fake", "fake-model")
    for text in ("This project uses pnpm", "The package manager is confirmed"):
        await store.append(record.id, "user_message", UserMessage(content=[TextBlock(text=text)]))
        await store.append(record.id, "assistant_message", AssistantMessage(
            items=[TextItem(text="confirmed")], provider="fake", model="fake-model",
            stop_reason="end_turn",
        ))
    return record


def extraction(text: str = "Use pnpm") -> dict[str, str]:
    return {"text": json.dumps({"summary": "package manager decision", "facts": [
        {"key": "package_manager", "text": text, "scope": "project"},
    ]}), "stop": "end_turn"}


def consolidated(record: SessionRecord, seq: int = 4, text: str = "Use pnpm") -> dict[str, str]:
    return {"text": json.dumps({"entries": [
        {"key": "package_manager", "text": text, "project_path": record.project_path,
         "sources": [f"{record.id}-{seq}"]},
    ]}), "stop": "end_turn"}


async def test_idle_session_generates_memory_without_user_request(
    store: EventStore, config: MemoryConfig, tmp_path: Path,
) -> None:
    record = await conversation(store, tmp_path)
    provider = FakeProvider([extraction(), consolidated(record)])
    worker = MemoryWorker(store, config)
    count = await worker.run_pass(lambda _: provider, lambda _: False)
    assert count == 1
    assert len(provider.requests) == 2
    assert all(request.tools == [] for request in provider.requests)
    assert "Use pnpm" in build_system_prompt(tmp_path, BUILD, config)
    assert len(await store.events(record.id)) == 4
    assert worker.archive.raw(f"{record.id}-4") is not None


async def test_restart_does_not_reextract_processed_source(
    store: EventStore, config: MemoryConfig, tmp_path: Path,
) -> None:
    record = await conversation(store, tmp_path)
    provider = FakeProvider([extraction(), consolidated(record)])
    await MemoryWorker(store, config).run_pass(lambda _: provider, lambda _: False)
    fresh = FakeProvider([])
    count = await MemoryWorker(store, config).run_pass(lambda _: fresh, lambda _: False)
    assert count == 0
    assert fresh.requests == []


@pytest.mark.parametrize("condition", ["active", "recent", "old", "short", "disabled"])
async def test_ineligible_session_never_calls_model(
    store: EventStore, config: MemoryConfig, tmp_path: Path, condition: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = await conversation(store, tmp_path)
    if condition == "recent":
        config = config.model_copy(update={"min_idle_hours": 6})
    if condition == "old":
        monkeypatch.setattr(
            "perdix.memory.worker.time.time", lambda: record.updated_at + 31 * 86400,
        )
    if condition == "short":
        config = config.model_copy(update={"min_user_turns": 3})
    if condition == "disabled":
        await store.append(record.id, "config_change", {"key": "memory_generate", "value": False})
    provider = FakeProvider([])
    count = await MemoryWorker(store, config).run_pass(
        lambda _: provider, lambda _: condition == "active",
    )
    assert count == 0
    assert provider.requests == []


async def test_failed_consolidation_retries_saved_extraction(
    store: EventStore, config: MemoryConfig, tmp_path: Path,
) -> None:
    record = await conversation(store, tmp_path)
    provider = FakeProvider([extraction(), {"text": "invalid JSON", "stop": "end_turn"},
                             consolidated(record)])
    worker = MemoryWorker(store, config)
    assert await worker.run_pass(lambda _: provider, lambda _: False) == 0
    assert await worker.run_pass(lambda _: provider, lambda _: False) == 1
    assert len(provider.requests) == 3


async def test_changed_source_is_not_published_after_generation(
    store: EventStore, config: MemoryConfig, tmp_path: Path,
) -> None:
    record = await conversation(store, tmp_path)
    class ChangingProvider(FakeProvider):
        async def stream(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
            async for event in super().stream(req):
                if isinstance(event, Stop) and len(self.requests) == 2:
                    await store.append(record.id, "user_message", UserMessage(
                        content=[TextBlock(text="New work started")]
                    ))
                yield event

    provider = ChangingProvider([extraction(), consolidated(record)])
    count = await MemoryWorker(store, config).run_pass(lambda _: provider, lambda _: False)
    assert count == 0
    assert MemoryArchive(config.root).snapshot().entries == []


async def test_consolidation_cannot_move_project_fact_to_global(
    store: EventStore, config: MemoryConfig, tmp_path: Path,
) -> None:
    record = await conversation(store, tmp_path)
    result = consolidated(record)
    body = json.loads(result["text"])
    body["entries"][0]["project_path"] = None
    result["text"] = json.dumps(body)
    provider = FakeProvider([extraction(), result])
    count = await MemoryWorker(store, config).run_pass(lambda _: provider, lambda _: False)
    assert count == 0
    assert MemoryArchive(config.root).snapshot().entries == []


async def test_project_memory_is_filtered_and_use_can_be_disabled(
    store: EventStore, config: MemoryConfig, tmp_path: Path,
) -> None:
    record = await conversation(store, tmp_path)
    provider = FakeProvider([extraction(), consolidated(record)])
    await MemoryWorker(store, config).run_pass(lambda _: provider, lambda _: False)
    assert "Use pnpm" not in build_system_prompt(tmp_path / "other", BUILD, config)
    assert "Use pnpm" not in build_system_prompt(
        tmp_path, BUILD, config.model_copy(update={"use_memories": False}),
    )


async def test_controls_restore_from_append_only_events(
    store: EventStore, config: MemoryConfig, tmp_path: Path,
) -> None:
    record = await conversation(store, tmp_path)
    controls = SessionMemories(config)
    await controls.set_option(store, record.id, "memory_generate", "off")
    await controls.set_option(store, record.id, "memory_use", "off")
    restored = SessionMemories(config)
    restored.restore(record.id, await store.events(record.id))
    assert {option.id: option.current_value for option in restored.options(record.id)} == {
        "memory_use": "off", "memory_generate": "off",
    }


async def test_rate_limit_defers_further_background_calls(
    store: EventStore, config: MemoryConfig, tmp_path: Path,
) -> None:
    await conversation(store, tmp_path)
    provider = FakeProvider([{"error": {"retryable": True, "status": 429}}])
    worker = MemoryWorker(store, config)
    assert await worker.run_pass(lambda _: provider, lambda _: False) == 0
    assert await worker.run_pass(lambda _: provider, lambda _: False) == 0
    assert len(provider.requests) == 1


async def test_secrets_are_redacted_before_model_call_and_persistence(
    store: EventStore, config: MemoryConfig, tmp_path: Path,
) -> None:
    record = await conversation(store, tmp_path)
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    await store.append(record.id, "user_message", UserMessage(content=[TextBlock(text=secret)]))
    await store.append(record.id, "assistant_message", AssistantMessage(
        items=[TextItem(text="received")], provider="fake", model="fake-model",
        stop_reason="end_turn",
    ))
    provider = FakeProvider([extraction(secret), consolidated(record, seq=6, text=secret)])
    worker = MemoryWorker(store, config)
    assert await worker.run_pass(lambda _: provider, lambda _: False) == 1
    assert secret not in provider.requests[0].view.model_dump_json()
    raw = worker.archive.raw(f"{record.id}-6")
    assert raw is not None
    assert secret not in raw.model_dump_json()
    assert secret not in worker.archive.snapshot().model_dump_json()


def test_empty_archive_does_not_create_files(config: MemoryConfig) -> None:
    assert MemoryArchive(config.root).snapshot() == MemorySnapshot()
    assert not config.root.exists()


async def test_new_source_version_replaces_view_but_retains_extraction_evidence(
    store: EventStore, config: MemoryConfig, tmp_path: Path,
) -> None:
    record = await conversation(store, tmp_path)
    provider = FakeProvider([extraction(), consolidated(record)])
    worker = MemoryWorker(store, config)
    await worker.run_pass(lambda _: provider, lambda _: False)
    await store.append(record.id, "user_message", UserMessage(
        content=[TextBlock(text="We migrated to npm")]
    ))
    await store.append(record.id, "assistant_message", AssistantMessage(
        items=[TextItem(text="verified")], provider="fake", model="fake-model",
        stop_reason="end_turn",
    ))
    update = FakeProvider([extraction("Use npm"), consolidated(record, seq=6, text="Use npm")])
    assert await worker.run_pass(lambda _: update, lambda _: False) == 1
    assert [entry.text for entry in worker.archive.snapshot().entries] == ["Use npm"]
    assert worker.archive.raw(f"{record.id}-4") is not None
    assert worker.archive.raw(f"{record.id}-6") is not None
    assert len(list((config.root / "snapshots").glob("*.json"))) == 2


async def test_background_close_cancels_call_and_releases_process_lock(
    store: EventStore, config: MemoryConfig, tmp_path: Path,
) -> None:
    await conversation(store, tmp_path)
    started = asyncio.Event()
    released = asyncio.Event()

    class WaitingProvider(FakeProvider):
        async def stream(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
            started.set()
            try:
                await asyncio.Event().wait()
                async for event in super().stream(req):
                    yield event
            finally:
                released.set()

    worker = MemoryWorker(store, config)
    worker.start(lambda _: WaitingProvider([]), lambda _: False)
    await asyncio.wait_for(started.wait(), 2)
    await worker.close()
    assert released.is_set()
    from filelock import FileLock

    with FileLock(str(config.root / "generation.lock"), timeout=0):
        assert worker.archive.snapshot().entries == []
