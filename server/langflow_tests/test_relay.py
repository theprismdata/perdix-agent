"""OpenAI 호환 중계: 변환과 HTTP 동작 (ADR 0027)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest

from perdix.config.agents import AgentsConfig
from perdix.context.messages import ToolResultsGroup, ToolUseItem
from perdix.llm.base import (
    ModelEvent,
    ModelRequest,
    ProviderError,
    Stop,
    TextDelta,
    ToolUseEnd,
    ToolUseStart,
    Usage,
)
from perdix.relay.app import create_app, load_or_create_token
from perdix.relay.wire import to_request


class ScriptedProvider:
    name = "scripted"

    def __init__(self, events: list[ModelEvent] | Exception) -> None:
        self.events = events
        self.requests: list[ModelRequest] = []

    def sanitize_tool_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        return schema

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(req)
        if isinstance(self.events, Exception):
            raise self.events
        for event in self.events:
            yield event


def client(provider: ScriptedProvider) -> httpx2.AsyncClient:
    app = create_app("tok", providers={"m": provider})
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://relay",
        headers={"authorization": "Bearer tok"},
    )


TOOL_BODY: dict[str, Any] = {
    "model": "m",
    "messages": [
        {"role": "system", "content": "규칙"},
        {"role": "user", "content": "계산해"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "calc", "arguments": '{"x":1}'}},
            {"id": "c2", "type": "function", "function": {"name": "calc", "arguments": '{"x":2}'}},
        ]},
        {"role": "tool", "tool_call_id": "c2", "content": "20"},
        {"role": "tool", "tool_call_id": "c1", "content": "10"},
    ],
    "tools": [{"type": "function", "function": {
        "name": "calc", "description": "계산", "parameters": {"type": "object"}}}],
}


def test_to_request_maps_roles_tools_and_orders_results() -> None:
    request = to_request(TOOL_BODY, ScriptedProvider([]), provider_name="x")
    assert request.system == "규칙"
    assert [t.name for t in request.tools] == ["calc"]
    kinds = [type(m).__name__ for m in request.view.messages]
    assert kinds == ["UserMessage", "AssistantMessage", "ToolResultsGroup"]
    assistant = request.view.messages[1]
    assert [i.id for i in assistant.items if isinstance(i, ToolUseItem)] == ["c1", "c2"]
    group = request.view.messages[2]
    assert isinstance(group, ToolResultsGroup)
    assert [(r.tool_use_id, r.index) for r in group.results] == [("c1", 0), ("c2", 1)]


@pytest.mark.asyncio
async def test_non_stream_returns_text_and_usage() -> None:
    provider = ScriptedProvider([
        TextDelta(text="안"), TextDelta(text="녕"), Usage(input_tokens=3, output_tokens=2),
        Stop(reason="end_turn"),
    ])
    async with client(provider) as http:
        response = await http.post("/v1/chat/completions", json={
            "model": "m", "messages": [{"role": "user", "content": "hi"}]})
    data = response.json()
    assert data["choices"][0]["message"]["content"] == "안녕"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"]["total_tokens"] == 5


@pytest.mark.asyncio
async def test_stream_tool_call_without_deltas_sends_full_arguments() -> None:
    provider = ScriptedProvider([
        ToolUseStart(id="c1", name="calc"), ToolUseEnd(id="c1", input={"x": 1}),
        Stop(reason="tool_use"),
    ])
    async with client(provider) as http:
        response = await http.post("/v1/chat/completions", json={**TOOL_BODY, "stream": True})
    lines = [ln[6:] for ln in response.text.splitlines() if ln.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    chunks = [json.loads(ln) for ln in lines[:-1]]
    calls = [c for ch in chunks for choice in ch["choices"]
             for c in choice["delta"].get("tool_calls", [])]
    assert calls[0]["function"]["name"] == "calc"
    assert json.loads(calls[1]["function"]["arguments"]) == {"x": 1}
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.asyncio
async def test_auth_unknown_model_and_provider_error() -> None:
    async with client(ScriptedProvider(ProviderError("크레딧 부족", retryable=False, status=402))) \
            as http:
        ok = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        assert (await http.post("/v1/chat/completions", json=ok,
                                headers={"authorization": "Bearer bad"})).status_code == 401
        assert (await http.post("/v1/chat/completions",
                                json={**ok, "model": "nope"})).status_code == 400
        prefixed = await http.post("/v1/chat/completions", json={**ok, "model": "perdix:m"})
        assert prefixed.status_code == 402
        failed = await http.post("/v1/chat/completions", json=ok)
        assert failed.status_code == 402 and "크레딧" in failed.json()["error"]["message"]


def test_token_file_created_private(tmp_path: Any) -> None:
    path = tmp_path / "relay-token"
    token = load_or_create_token(path)
    assert len(token) >= 32 and load_or_create_token(path) == token
    assert path.stat().st_mode & 0o777 == 0o600


def mcp_client(tmp_path: Any, mode: str = "deny") -> httpx2.AsyncClient:
    app = create_app("tok", providers={}, workspace=tmp_path,
                     agents_config=AgentsConfig(), permission_mode=mode)  # type: ignore[arg-type]
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://relay",
                              headers={"authorization": "Bearer tok"})


def call(http: httpx2.AsyncClient, role: str, name: str, arguments: dict[str, Any]) -> Any:
    return http.post(f"/mcp/{role}", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                           "params": {"name": name, "arguments": arguments}})


@pytest.mark.asyncio
async def test_agent_definitions_match_perdix_roles(tmp_path: Any) -> None:
    async with mcp_client(tmp_path) as http:
        roles = (await http.get("/v1/agents")).json()["data"]
        assert {r["role"] for r in roles} >= {"chat", "plan", "build", "explore", "executor"}
        explore = (await http.get("/v1/agents/explore")).json()
        executor = (await http.get("/v1/agents/executor")).json()
        assert explore["readOnly"] and not executor["readOnly"]
        explore_tools = {t["name"] for t in explore["tools"]}
        executor_tools = {t["name"] for t in executor["tools"]}
        assert "read" in explore_tools and not {"write", "edit", "bash"} & explore_tools
        assert {"read", "write", "edit", "bash"} <= executor_tools
        assert "task" not in executor_tools and "task" in executor["omittedTools"]
        assert "Mode: " in executor["systemPrompt"] and "You are Perdix" in executor["systemPrompt"]
        assert (await http.get("/v1/agents/nope")).status_code == 404


@pytest.mark.asyncio
async def test_mcp_tools_follow_role_and_permission_policy(tmp_path: Any) -> None:
    (tmp_path / "a.txt").write_text("hello\n")
    async with mcp_client(tmp_path) as http:
        assert (await http.post("/mcp/chat", json={}, headers={"authorization": "x"})
                ).status_code == 401
        note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert (await http.post("/mcp/chat", json=note)).status_code == 202
        listed = (await http.post("/mcp/explore", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/list"})).json()["result"]["tools"]
        assert "write" not in {t["name"] for t in listed}
        read = (await call(http, "explore", "read", {"path": "a.txt"})).json()["result"]
        assert "hello" in read["content"][0]["text"] and not read["isError"]
        escape = (await call(http, "explore", "read", {"path": "../../etc/hosts"})).json()
        assert escape["result"]["isError"]
        # 읽기 전용 역할에는 쓰기 도구가 없다
        assert (await call(http, "explore", "write", {"path": "x", "content": "y"})
                ).json()["result"]["isError"]
        # 쓰기 가능한 역할이라도 deny 모드에서는 정책이 거부한다
        denied = (await call(http, "executor", "write", {"path": "x", "content": "y"})).json()
        text = denied["result"]["content"][0]["text"]
        assert denied["result"]["isError"] and "permission policy" in text
        assert not (tmp_path / "x").exists()
    async with mcp_client(tmp_path, "accept-edits") as http:
        ok = (await call(http, "executor", "write", {"path": "x", "content": "y"})).json()
        assert not ok["result"]["isError"] and (tmp_path / "x").read_text() == "y"
        bash = (await call(http, "executor", "bash", {"command": "echo hi"})).json()
        assert bash["result"]["isError"]  # accept-edits는 셸을 허용하지 않는다


@pytest.mark.asyncio
async def test_role_model_alias_resolves_to_assigned_model(tmp_path: Any) -> None:
    agents = AgentsConfig.model_validate({
        "models": {"fast": {"provider": "openrouter", "model": "vendor/fast-model",
                            "capabilities": {"id": "vendor/fast-model", "provider": "openrouter",
                                             "context_window": 1000, "max_output": 100,
                                             "input_price": 0, "output_price": 0,
                                             "supports_effort": False}}},
        "agents": {"reviewer": {"model": "fast"}}})
    provider = ScriptedProvider([TextDelta(text="ok"), Stop(reason="end_turn")])
    app = create_app("tok", providers={"vendor/fast-model": provider}, workspace=tmp_path,
                     agents_config=agents)
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),
                                  base_url="http://relay",
                                  headers={"authorization": "Bearer tok"}) as http:
        response = await http.post("/v1/chat/completions", json={
            "model": "perdix:role/reviewer", "messages": [{"role": "user", "content": "hi"}]})
        assert response.json()["choices"][0]["message"]["content"] == "ok"
        assert provider.requests
        bad = await http.post("/v1/chat/completions", json={
            "model": "perdix:role/nope", "messages": [{"role": "user", "content": "hi"}]})
        assert bad.status_code == 400
