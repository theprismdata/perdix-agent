import json
from pathlib import Path

import pytest
from filelock import FileLock

from perdix.context.builder import build_system_prompt
from perdix.harness.modes import BUILD
from perdix.llm.fake import FakeProvider
from perdix.memory.schema import MemoryConfig
from perdix.memory.worker import MemoryWorker
from perdix.session.store import EventStore
from test_automatic_memory import consolidated, conversation, extraction


async def test_global_and_project_facts_keep_separate_scopes(tmp_path: Path) -> None:
    config = MemoryConfig(root=tmp_path / "memories", min_idle_hours=0)
    store = await EventStore.open(":memory:")
    try:
        record = await conversation(store, tmp_path)
        source = f"{record.id}-4"
        provider = FakeProvider([
            {"text": json.dumps({"facts": [
                {"key": "package_manager", "text": "project-pnpm", "scope": "project"},
                {"key": "language", "text": "global-korean", "scope": "global"},
            ]})},
            {"text": json.dumps({"entries": [
                {"key": "package_manager", "text": "project-pnpm",
                 "project_path": str(tmp_path), "sources": [source]},
                {"key": "language", "text": "global-korean",
                 "project_path": None, "sources": [source]},
            ]})},
        ])
        count = await MemoryWorker(store, config).run_pass(lambda _: provider, lambda _: False)
        assert count == 1
        assert "global-korean" in build_system_prompt(tmp_path / "other", BUILD, config)
        assert "project-pnpm" not in build_system_prompt(tmp_path / "other", BUILD, config)
    finally:
        await store.close()


@pytest.mark.parametrize("failure", ["tools", "truncated", "duplicates", "empty"])
async def test_invalid_model_output_does_not_publish_snapshot(tmp_path: Path, failure: str) -> None:
    config = MemoryConfig(root=tmp_path / "memories", min_idle_hours=0)
    store = await EventStore.open(":memory:")
    try:
        record = await conversation(store, tmp_path)
        responses = [extraction(), consolidated(record)]
        if failure == "tools":
            responses = [{"tool_uses": [{"id": "bad", "name": "bash",
                                        "input": {"command": "touch unexpected"}}]}]
        if failure == "truncated":
            responses[0]["stop"] = "max_tokens"
        if failure in ("duplicates", "empty"):
            result = json.loads(responses[1]["text"])
            result["entries"] = result["entries"] * 2 if failure == "duplicates" else []
            responses[1]["text"] = json.dumps(result)
        worker = MemoryWorker(store, config)
        provider = FakeProvider(responses)
        count = await worker.run_pass(lambda _: provider, lambda _: False)
        assert count == 0
        assert worker.archive.snapshot().entries == []
        assert not (tmp_path / "unexpected").exists()
    finally:
        await store.close()


async def test_other_server_lock_prevents_duplicate_model_work(tmp_path: Path) -> None:
    config = MemoryConfig(root=tmp_path / "memories", min_idle_hours=0)
    config.root.mkdir()
    store = await EventStore.open(":memory:")
    try:
        await conversation(store, tmp_path)
        provider = FakeProvider([])
        with FileLock(str(config.root / "generation.lock"), timeout=0):
            count = await MemoryWorker(store, config).run_pass(lambda _: provider, lambda _: False)
        assert count == 0
        assert provider.requests == []
    finally:
        await store.close()
