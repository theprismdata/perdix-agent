import asyncio
from pathlib import Path

from acp import PROTOCOL_VERSION, text_block

from test_acp_e2e import TIMEOUT, RecordingClient, spawn_server


async def test_memory_survives_server_restart_and_is_read_in_new_session(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    db = tmp_path / "sessions.db"
    writer = RecordingClient()
    async with spawn_server(writer, [
        {"tool_uses": [{"id": "save", "name": "memory_append",
                        "input": {"content": "acp-memory-sentinel"}}], "stop": "tool_use"},
        {"text": "saved", "stop": "end_turn"},
    ], tmp_path, db) as (conn, _):
        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        session = await conn.new_session(cwd=str(project))
        await conn.set_session_mode(session_id=session.session_id, mode_id="build")
        result = await asyncio.wait_for(conn.prompt(
            session_id=session.session_id, prompt=[text_block("remember memory-sentinel")]
        ), TIMEOUT)
        assert result.stop_reason == "end_turn"
        assert len(writer.permission_requests) == 1
    reader = RecordingClient()
    async with spawn_server(reader, [
        {"tool_uses": [
            {"id": "read", "name": "memory_read", "input": {}},
            {"id": "search", "name": "session_search",
             "input": {"query": "memory-sentinel", "scope": "project"}},
        ], "stop": "tool_use"},
        {"text": "recalled", "stop": "end_turn"},
    ], tmp_path, db) as (conn, _):
        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        second = await conn.new_session(cwd=str(project))
        result = await asyncio.wait_for(conn.prompt(
            session_id=second.session_id, prompt=[text_block("recall")]
        ), TIMEOUT)
        assert result.stop_reason == "end_turn"
        completed = {
            update.tool_call_id: update for update in reader.of_type("tool_call_update")
            if update.status == "completed"
        }
        assert "acp-memory-sentinel" in completed["read"].model_dump_json()
        assert session.session_id in completed["search"].model_dump_json()
        assert not reader.permission_requests
