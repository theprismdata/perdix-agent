import asyncio
import json
import sys
from pathlib import Path

from acp import PROTOCOL_VERSION, text_block
from acp.stdio import spawn_agent_process

from perdix.memory.archive import MemoryArchive
from perdix.memory.schema import MemoryConfig
from perdix.session.store import EventStore
from test_acp_e2e import TIMEOUT, RecordingClient
from test_automatic_memory import consolidated, conversation, extraction


async def test_server_automatically_extracts_old_chat_and_recalls_it_over_stdio(
    tmp_path: Path,
) -> None:
    project = (tmp_path / "project").resolve()
    project.mkdir()
    db = tmp_path / "sessions.db"
    store = await EventStore.open(db)
    try:
        old = await conversation(store, project)
    finally:
        await store.close()
    memory_script = tmp_path / "memory-script.json"
    memory_script.write_text(json.dumps([extraction(), consolidated(old)]), encoding="utf-8")
    config = MemoryConfig(root=tmp_path / "memories", min_idle_hours=0, fake_script=memory_script)
    config_path = tmp_path / "memory.json"
    config_path.write_text(config.model_dump_json(), encoding="utf-8")
    script = tmp_path / "chat-script.json"
    script.write_text(json.dumps([
        {"tool_uses": [{"id": "recall", "name": "memory_read", "input": {}}],
         "stop": "tool_use"},
        {"text": "done", "stop": "end_turn"},
    ]), encoding="utf-8")
    client = RecordingClient()
    async with spawn_agent_process(
        client, sys.executable, "-m", "perdix", "--provider", "fake",
        "--fake-script", str(script), "--memory-config", str(config_path), "--db", str(db),
        use_unstable_protocol=True,
    ) as (conn, _):
        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        new = await conn.new_session(cwd=str(project))
        archive = MemoryArchive(config.root)

        async def await_background_memory() -> None:
            while not archive.snapshot().entries:
                await conn.prompt(session_id=new.session_id, prompt=[text_block("/memories")])

        await asyncio.wait_for(await_background_memory(), TIMEOUT)
        result = await asyncio.wait_for(conn.prompt(
            session_id=new.session_id, prompt=[text_block("What package manager do we use?")],
        ), TIMEOUT)
        assert result.stop_reason == "end_turn"
        completed = [update for update in client.of_type("tool_call_update")
                     if update.status == "completed" and update.tool_call_id == "recall"]
        assert len(completed) == 1
        assert "Use pnpm" in completed[0].model_dump_json()
        assert not client.permission_requests
        assert archive.raw(f"{old.id}-4") is not None
        changed = await conn.set_config_option(
            session_id=new.session_id, config_id="memory_generate", value="off",
        )
        assert changed is not None
        assert any(option.id == "memory_generate" and option.current_value == "off"
                   for option in changed.config_options)
    client = RecordingClient()
    async with spawn_agent_process(
        client, sys.executable, "-m", "perdix", "--provider", "fake",
        "--fake-script", str(script), "--memory-config", str(config_path), "--db", str(db),
        use_unstable_protocol=True,
    ) as (conn, _):
        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        loaded = await conn.load_session(session_id=new.session_id, cwd=str(project))
        assert loaded is not None
        assert any(option.id == "memory_generate" and option.current_value == "off"
                   for option in loaded.config_options or [])
        assert len(MemoryArchive(config.root).snapshot().entries) == 1
