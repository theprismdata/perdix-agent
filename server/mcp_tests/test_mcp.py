from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from perdix.mcp.client import McpCancelled, McpClient, McpError, build_env
from perdix.mcp.config import (
    McpServerConfig,
    load_servers_file,
    parse_servers,
    resolve_servers,
    servers_from_acp,
)
from perdix.mcp.hub import McpHub
from perdix.mcp.tool import MAX_RESULT_CHARS, McpTool, render_result, tool_name
from perdix.sandbox.local import LocalBackend
from perdix.tools.base import ToolContext, validate_input

FAKE = str(Path(__file__).with_name("fake_mcp_server.py"))


def config(**extra: Any) -> McpServerConfig:
    env = extra.pop("env", {})
    return McpServerConfig(command=sys.executable, args=(FAKE,), env=env, **extra)


def context(cwd: Path, cancel: asyncio.Event | None = None) -> ToolContext:
    return ToolContext(cwd=cwd, sandbox=LocalBackend(), cancel=cancel or asyncio.Event())


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[McpClient]:
    connection = McpClient("fake", config(), tmp_path)
    await connection.start()
    try:
        yield connection
    finally:
        await connection.close()


# ------------------------------------------------------------------ 설정


def test_parse_accepts_claude_format_and_skips_non_stdio() -> None:
    servers, warnings = parse_servers({"mcpServers": {
        "docs": {"command": "autorag-mcp", "args": ["--x"], "env": {"A": "1"}, "readOnly": True},
        "remote": {"type": "http", "url": "https://example.com/mcp"},
        "bad name!": {"command": "x"},
        "off": {"command": "x", "enabled": False},
        "broken": {"command": "x", "timeout": -1},
    }}, "test")
    assert set(servers) == {"docs"}
    assert servers["docs"].read_only is True
    assert servers["docs"].args == ("--x",)
    assert any("remote" in w and "stdio" in w for w in warnings)
    assert any("bad name" in w for w in warnings)
    assert any("broken" in w for w in warnings)


def test_hint_is_added_to_tool_descriptions() -> None:
    entry = {"command": "x", "hint": "query만 보내세요"}
    servers, _ = parse_servers({"mcpServers": {"docs": entry}}, "t")
    assert servers["docs"].hint == "query만 보내세요"
    tool = McpTool(name="n", server="docs", remote_name="r", description="검색",
                   input_schema={}, client=None, read_only=True,  # type: ignore[arg-type]
                   hint=servers["docs"].hint)
    assert tool.description == "[MCP docs] 검색\n사용 안내: query만 보내세요"
    assert McpTool(name="n", server="docs", remote_name="r", description="검색", input_schema={},
                   client=None, read_only=True).description == "[MCP docs] 검색"  # type: ignore[arg-type]


def test_omit_params_hides_optional_properties_but_never_required_ones() -> None:
    schema = {"type": "object", "required": ["query"], "additionalProperties": False,
              "properties": {"query": {"type": "string"}, "scope": {"type": "string"},
                             "methods": {"type": "array"}}}
    tool = McpTool(name="n", server="docs", remote_name="search", description="d",
                   input_schema=schema, client=None,  # type: ignore[arg-type]
                   read_only=True, omit_params=("scope", "methods", "query"))
    assert set(tool.input_schema["properties"]) == {"query"}
    assert validate_input(tool, {"query": "q"}) == []
    assert validate_input(tool, {"query": "q", "scope": "all"})  # 숨긴 인수는 거절한다
    entry = {"command": "x", "omitParams": {"search": ["scope"]}}
    servers, _ = parse_servers({"mcpServers": {"docs": entry}}, "t")
    assert servers["docs"].omit_params == {"search": ("scope",)}


def test_untrusted_project_file_is_not_loaded(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"evil": {"command": "rm"}}}), encoding="utf-8")
    servers, warnings = resolve_servers(
        tmp_path, trust_project=False, global_path=tmp_path / "none.json")
    assert servers == {}
    assert any("신뢰하지 않아" in w for w in warnings)
    trusted, _ = resolve_servers(tmp_path, trust_project=True, global_path=tmp_path / "none.json")
    assert set(trusted) == {"evil"}


def test_precedence_client_over_project_over_global(tmp_path: Path) -> None:
    glob = tmp_path / "global.json"
    glob.write_text(json.dumps({"mcpServers": {
        "a": {"command": "global-a"}, "b": {"command": "global-b"}, "c": {"command": "global-c"},
    }}), encoding="utf-8")
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "b": {"command": "project-b"}, "c": {"command": "project-c"}}}), encoding="utf-8")
    servers, _ = resolve_servers(
        tmp_path, trust_project=True, global_path=glob,
        client={"c": McpServerConfig(command="client-c")})
    assert {k: v.command for k, v in servers.items()} == {
        "a": "global-a", "b": "project-b", "c": "client-c"}


def test_bad_or_huge_config_files_do_not_raise(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_servers_file(bad)[0] == {}
    assert load_servers_file(bad)[1]
    assert load_servers_file(tmp_path / "missing.json") == ({}, [])


def test_servers_from_acp_converts_stdio_only() -> None:
    stdio = SimpleNamespace(name="docs", command="cmd", args=["a"],
                            env=[SimpleNamespace(name="K", value="V")])
    http = SimpleNamespace(name="web", url="https://example.com", headers=[])
    servers = servers_from_acp([stdio, http, SimpleNamespace(name="bad name", command="x")])
    assert set(servers) == {"docs"}
    assert servers["docs"].env == {"K": "V"}
    assert servers_from_acp(None) == {}


# ------------------------------------------------------------------ 클라이언트


@pytest.mark.asyncio
async def test_initialize_list_with_pagination_and_call(client: McpClient) -> None:
    assert client.has_tools
    tools = await client.list_tools()
    assert [t["name"] for t in tools][:2] == ["echo", "fail"]
    assert len(tools) == 9
    result = await client.call_tool("echo", {"text": "안녕"})
    assert result["content"][0]["text"] == "echo: 안녕"


@pytest.mark.asyncio
async def test_non_json_stdout_is_ignored_and_stderr_is_captured(client: McpClient) -> None:
    await client.call_tool("echo", {"text": "x"})
    assert "fake server starting" in client.stderr_tail


@pytest.mark.asyncio
async def test_server_requests_get_ping_result_and_method_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "log.jsonl"
    connection = McpClient("fake", config(env={"FAKE_MCP_LOG": str(log)}), tmp_path)
    await connection.start()
    try:
        await connection.list_tools()
        await asyncio.sleep(0.2)
    finally:
        await connection.close()
    entries = [json.loads(line)["raw"] for line in log.read_text().splitlines()]
    ping = next(e for e in entries if e.get("id") == 900)
    sampling = next(e for e in entries if e.get("id") == 901)
    assert ping == {"jsonrpc": "2.0", "id": 900, "result": {}}
    assert sampling["error"]["code"] == -32601
    methods = [e["method"] for e in entries if "method" in e]
    assert methods[:2] == ["initialize", "notifications/initialized"]
    initialize = entries[0]["params"]
    assert initialize["protocolVersion"] == "2025-06-18"
    assert initialize["capabilities"] == {}


@pytest.mark.asyncio
async def test_unsupported_protocol_version_is_rejected(tmp_path: Path) -> None:
    connection = McpClient("fake", config(env={"FAKE_MCP_VERSION": "1999-01-01"}), tmp_path)
    with pytest.raises(McpError, match="프로토콜 버전"):
        await connection.start()
    assert not connection.alive


@pytest.mark.asyncio
async def test_missing_command_is_a_clear_error(tmp_path: Path) -> None:
    connection = McpClient("ghost", McpServerConfig(command="/nonexistent/mcp-server"), tmp_path)
    with pytest.raises(McpError, match="시작하지 못했습니다"):
        await connection.start()


@pytest.mark.asyncio
async def test_server_without_tools_capability_lists_nothing(tmp_path: Path) -> None:
    connection = McpClient("fake", config(env={"FAKE_MCP_NO_TOOLS": "1"}), tmp_path)
    await connection.start()
    try:
        assert await connection.list_tools() == []
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_rpc_error_becomes_mcp_error(client: McpClient) -> None:
    with pytest.raises(McpError, match="unknown tool"):
        await client.call_tool("rpcerror", {})


@pytest.mark.asyncio
async def test_timeout_sends_cancelled_notification(tmp_path: Path) -> None:
    log = tmp_path / "log.jsonl"
    connection = McpClient("fake", config(env={"FAKE_MCP_LOG": str(log)}, timeout=1), tmp_path)
    await connection.start()
    try:
        with pytest.raises(McpError, match="안에 끝나지 않았습니다"):
            await connection.call_tool("slow", {}, timeout=0.5)
    finally:
        await connection.close()
    entries = [json.loads(line)["raw"] for line in log.read_text().splitlines()]
    call_id = next(e["id"] for e in entries if e.get("method") == "tools/call")
    cancelled = [e for e in entries if e.get("method") == "notifications/cancelled"]
    assert cancelled and cancelled[0]["params"] == {"requestId": call_id, "reason": "timeout"}


@pytest.mark.asyncio
async def test_cancel_event_aborts_call(client: McpClient) -> None:
    cancel = asyncio.Event()
    task = asyncio.create_task(client.call_tool("slow", {}, cancel=cancel, timeout=20))
    await asyncio.sleep(0.3)
    cancel.set()
    with pytest.raises(McpCancelled):
        await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_server_exit_during_call_fails_fast(client: McpClient) -> None:
    with pytest.raises(McpError, match="종료되었습니다"):
        await asyncio.wait_for(client.call_tool("die", {}), 5)
    assert not client.alive


@pytest.mark.asyncio
async def test_close_terminates_the_process_group(tmp_path: Path) -> None:
    connection = McpClient("fake", config(), tmp_path)
    await connection.start()
    proc = connection._proc
    assert proc is not None
    await connection.close()
    assert proc.returncode is not None


# ------------------------------------------------------------------ 환경 변수(규칙 12)


def test_build_env_strips_secrets_but_keeps_explicit_values() -> None:
    base: Mapping[str, str] = {
        "PATH": "/usr/bin", "HOME": "/h", "OPENAI_API_KEY": "sk-1", "MY_TOKEN": "t",
        "DB_PASSWORD": "p", "KEEP_ME": "ok",
    }
    env = build_env(McpServerConfig(command="x", env={"EXPLICIT_TOKEN": "needed"}), base)
    assert "OPENAI_API_KEY" not in env and "MY_TOKEN" not in env and "DB_PASSWORD" not in env
    assert env["KEEP_ME"] == "ok" and env["EXPLICIT_TOKEN"] == "needed"


@pytest.mark.asyncio
async def test_child_process_does_not_see_parent_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PERDIX_TEST_API_KEY", "must-not-leak")
    monkeypatch.setenv("PERDIX_TEST_VISIBLE", "fine")
    connection = McpClient("fake", config(env={"EXPLICIT_VALUE": "yes"}), tmp_path)
    await connection.start()
    try:
        result = await connection.call_tool("env", {})
    finally:
        await connection.close()
    names = json.loads(result["content"][0]["text"])
    assert "PERDIX_TEST_API_KEY" not in names
    assert "PERDIX_TEST_VISIBLE" in names and "EXPLICIT_VALUE" in names


# ------------------------------------------------------------------ 도구 어댑터


def test_tool_names_are_provider_safe_and_unique() -> None:
    taken: set[str] = set()
    first = tool_name("docs", "weird/name.with chars", taken)
    assert first == "mcp__docs__weird_name_with_chars"
    taken.add(first)
    assert tool_name("docs", "weird_name_with_chars", taken) != first
    long = tool_name("docs", "x" * 100, set())
    assert len(long) <= 64 and long.startswith("mcp__docs__")
    assert all(c.isalnum() or c in "_-" for c in long)
    assert tool_name("a", "b" * 100, set()) != tool_name("a", "b" * 101, set())


def test_render_result_handles_all_block_types_and_truncates() -> None:
    text = render_result({"content": [
        {"type": "text", "text": "hello"},
        {"type": "image", "mimeType": "image/png", "data": "AAAA"},
        {"type": "resource_link", "uri": "file:///a"},
        {"type": "resource", "resource": {"uri": "file:///b", "text": "body"}},
    ]})
    assert text.splitlines() == ["hello", "[image image/png 생략]", "[resource] file:///a", "body"]
    assert "42" in render_result({"content": [], "structuredContent": {"n": 42}})
    big = render_result({"content": [{"type": "text", "text": "x" * (MAX_RESULT_CHARS + 5000)}]})
    assert len(big) < MAX_RESULT_CHARS + 200 and "잘랐습니다" in big


@pytest.mark.asyncio
async def test_tool_run_maps_results_errors_and_validates_input(
    client: McpClient, tmp_path: Path
) -> None:
    tools = {t["name"]: t for t in await client.list_tools()}

    def make(name: str, read_only: bool = False) -> McpTool:
        return McpTool(name=f"mcp__fake__{name}", server="fake", remote_name=name,
                       description=tools[name]["description"],
                       input_schema=tools[name]["inputSchema"],
                       client=client, read_only=read_only)

    echo = make("echo")
    assert validate_input(echo, {}) and validate_input(echo, {"text": 1})
    assert not validate_input(echo, {"text": "ok"})
    out = await echo.run(context(tmp_path), {"text": "hi"})
    assert out.content == "echo: hi" and not out.is_error
    failed = await make("fail").run(context(tmp_path), {})
    assert failed.is_error and "의도한 실패" in failed.content
    rpc = await make("rpcerror").run(context(tmp_path), {})
    assert rpc.is_error and "unknown tool" in rpc.content
    big = await make("big").run(context(tmp_path), {})
    assert not big.is_error and len(big.content) <= MAX_RESULT_CHARS + 200
    image = await make("image").run(context(tmp_path), {})
    assert "생략" in image.content


@pytest.mark.asyncio
async def test_annotations_are_not_trusted_for_read_only(client: McpClient) -> None:
    tools = {t["name"]: t for t in await client.list_tools()}
    assert tools["echo"]["annotations"]["readOnlyHint"] is True
    untrusted = McpTool(name="n", server="fake", remote_name="echo", description="d",
                        input_schema=tools["echo"]["inputSchema"], client=client, read_only=False)
    assert untrusted.is_read_only({}) is False and untrusted.kind == "other"
    declared = McpTool(name="n", server="fake", remote_name="echo", description="d",
                       input_schema={}, client=client, read_only=True)
    assert declared.is_read_only({}) is True and declared.kind == "search"
    assert declared.input_schema == {"type": "object"}


@pytest.mark.asyncio
async def test_cancelled_tool_call_returns_error_output(client: McpClient, tmp_path: Path) -> None:
    tool = McpTool(name="n", server="fake", remote_name="slow", description="d",
                   input_schema={"type": "object"}, client=client, read_only=False)
    cancel = asyncio.Event()
    task = asyncio.create_task(tool.run(context(tmp_path, cancel), {}))
    await asyncio.sleep(0.3)
    cancel.set()
    out = await asyncio.wait_for(task, 5)
    assert out.is_error and "취소" in out.content


# ------------------------------------------------------------------ 허브


@pytest.mark.asyncio
async def test_hub_builds_tools_reuses_connections_and_tolerates_failures(tmp_path: Path) -> None:
    glob = tmp_path / "mcp.json"
    glob.write_text(json.dumps({"mcpServers": {
        "good": {"command": sys.executable, "args": [FAKE], "readOnly": True},
        "ghost": {"command": "/nonexistent/server"},
    }}), encoding="utf-8")
    hub = McpHub(global_path=glob)
    try:
        first = await hub.tools_for(tmp_path, trust_project=False)
        names = [t.name for t in first.tools]
        assert "mcp__good__echo" in names and "mcp__good__weird_name_with_chars" in names
        assert all(t.kind == "search" for t in first.tools)
        assert first.auto_allow == set(names)
        assert any("ghost" in w for w in first.warnings)
        clients = {id(c) for c in hub._clients.values()}
        second = await hub.tools_for(tmp_path, trust_project=False)
        assert {id(c) for c in hub._clients.values()} == clients
        assert [t.name for t in second.tools] == names
        procs = [c._proc for c in hub._clients.values() if c._proc is not None]
    finally:
        await hub.aclose()
    assert procs and all(p.returncode is not None for p in procs)


@pytest.mark.asyncio
async def test_hub_restarts_a_dead_server(tmp_path: Path) -> None:
    glob = tmp_path / "mcp.json"
    glob.write_text(json.dumps({"mcpServers": {
        "good": {"command": sys.executable, "args": [FAKE]}}}), encoding="utf-8")
    hub = McpHub(global_path=glob)
    try:
        first = await hub.tools_for(tmp_path, trust_project=False)
        echo = next(t for t in first.tools if t.name == "mcp__good__echo")
        old = echo._client  # type: ignore[attr-defined]
        await old.close()
        second = await hub.tools_for(tmp_path, trust_project=False)
        again = next(t for t in second.tools if t.name == "mcp__good__echo")
        assert again._client is not old and again._client.alive  # type: ignore[attr-defined]
        assert not first.auto_allow
    finally:
        await hub.aclose()


@pytest.mark.asyncio
async def test_project_mcp_json_needs_trust(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "proj": {"command": sys.executable, "args": [FAKE]}}}), encoding="utf-8")
    hub = McpHub(global_path=tmp_path / "none.json")
    try:
        untrusted = await hub.tools_for(tmp_path, trust_project=False)
        assert untrusted.tools == [] and not hub._clients
        trusted = await hub.tools_for(tmp_path, trust_project=True)
        assert any(t.name == "mcp__proj__echo" for t in trusted.tools)
    finally:
        await hub.aclose()


# ------------------------------------------------------------------ 에이전트 통합


async def run_agent_turn(
    tmp_path: Path, *, permission_mode: str, mcp_global: dict[str, Any] | None,
    acp_servers: list[Any] | None,
) -> tuple[Any, list[dict[str, Any]]]:
    from acp.helpers import text_block

    from perdix.acp.agent import PerdixAgent
    from perdix.config.schema import ServerConfig
    from perdix.memory.schema import MemoryConfig

    script = tmp_path / "script.json"
    script.write_text(json.dumps([
        {"tool_uses": [{"id": "t1", "name": "mcp__fake__echo", "input": {"text": "지식"}}],
         "stop": "tool_use"},
        {"text": "완료", "stop": "end_turn"},
    ]), encoding="utf-8")
    agents = tmp_path / "agents.json"
    agents.write_text("{}", encoding="utf-8")
    glob = tmp_path / "global-mcp.json"
    if mcp_global is not None:
        glob.write_text(json.dumps({"mcpServers": mcp_global}), encoding="utf-8")
    agent = PerdixAgent(ServerConfig(
        provider="fake", fake_script=script, permission_mode=permission_mode,  # type: ignore[arg-type]
        db_path=str(tmp_path / "events.db"),
        memories=MemoryConfig(enabled=False, root=tmp_path / "memories"),
        credentials_path=tmp_path / "credentials.json", agents_config_path=agents,
    ))
    agent._mcp = McpHub(global_path=glob)
    try:
        session = await agent.new_session(cwd=str(tmp_path), mcp_servers=acp_servers)
        assert "mcp__fake__echo" in [t.name for t in agent._sessions[session.session_id].mcp_tools]
        response = await agent.prompt(session.session_id, [text_block("에코해줘")])
        assert response.stop_reason == "end_turn"
        store = await agent._get_store()
        events = await store.events(session.session_id)
        return agent, [e.payload for e in events if e.kind == "tool_result"]
    finally:
        await agent.aclose()


@pytest.mark.asyncio
async def test_read_only_server_tool_is_allowed_and_runs(tmp_path: Path) -> None:
    _, results = await run_agent_turn(
        tmp_path, permission_mode="interactive", acp_servers=None,
        mcp_global={"fake": {"command": sys.executable, "args": [FAKE], "readOnly": True}},
    )
    assert results and "echo: 지식" in json.dumps(results, ensure_ascii=False)
    assert '"is_error": true' not in json.dumps(results)


@pytest.mark.asyncio
async def test_undeclared_server_tool_is_denied_without_user_approval(tmp_path: Path) -> None:
    from acp import schema

    _, results = await run_agent_turn(
        tmp_path, permission_mode="deny", mcp_global=None,
        acp_servers=[schema.McpServerStdio(name="fake", command=sys.executable, args=[FAKE],
                                           env=[])],
    )
    dumped = json.dumps(results, ensure_ascii=False)
    assert results and "echo: 지식" not in dumped
    assert '"is_error": true' in dumped
