"""Langflow 어댑터 테스트. 응답 형태는 1.13.0 실서버에서 기록한 것이다(ADR 0026)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import ValidationError

from perdix.integrations.langflow.client import LangflowClient
from perdix.integrations.langflow.config import LangflowConfig, parse_version
from perdix.integrations.langflow.errors import LangflowError
from perdix.sandbox.local import LocalBackend
from perdix.tools.base import ToolContext
from perdix.tools.langflow import LangflowRunTool

FLOW = "28549500-69e9-465f-aa8a-4486102151e4"
VERSION = {"version": "1.13.0", "main_version": "1.13.0", "package": "Langflow"}


def message(text: str, error: bool = False) -> dict[str, Any]:
    return {"results": {"message": {"text_key": "text", "data": {"text": text, "error": error}}}}


def ok_body(text: str = "hello") -> dict[str, Any]:
    return {"session_id": "s1", "outputs": [{"inputs": {}, "outputs": [message(text)]}]}


def config(**over: Any) -> LangflowConfig:
    return LangflowConfig(enabled=True, flows={"echo": FLOW}, **over)


class Fake:
    """경로별 응답을 돌려주는 가짜 Langflow. 호출 기록을 남긴다."""

    def __init__(self, run: Any = None, version: Any = None) -> None:
        self.calls: list[httpx2.Request] = []
        self.run = run if run is not None else httpx2.Response(200, json=ok_body())
        self.version = version if version is not None else httpx2.Response(200, json=VERSION)

    def transport(self) -> httpx2.MockTransport:
        def handler(request: httpx2.Request) -> httpx2.Response:
            self.calls.append(request)
            if isinstance(self.run, Exception):
                raise self.run
            if request.url.path == "/api/v1/version":
                return self.version
            return self.run

        return httpx2.MockTransport(handler)


def client(fake: Fake, **over: Any) -> LangflowClient:
    return LangflowClient(config(**over), transport=fake.transport())


@pytest.mark.asyncio
async def test_run_returns_text_and_sends_expected_request() -> None:
    fake = Fake()
    result = await client(fake).run("echo", "hi", "sess")
    assert (result.text, result.session_id, result.truncated) == ("hello", "s1", False)
    sent = fake.calls[-1]
    assert sent.url.path == f"/api/v1/run/{FLOW}"
    assert json.loads(sent.content) == {
        "input_value": "hi", "input_type": "chat", "output_type": "chat", "session_id": "sess",
    }


@pytest.mark.asyncio
async def test_unknown_alias_and_bad_input_never_reach_network() -> None:
    fake = Fake()
    c = client(fake, max_input_chars=5)
    for alias, text, code in [("nope", "x", "unknown_flow"), ("echo", "  ", "invalid_input"),
                              ("echo", "123456", "invalid_input")]:
        with pytest.raises(LangflowError) as exc:
            await c.run(alias, text)
        assert exc.value.code == code
    assert fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("response", "code"), [
    (httpx2.Response(403, json={"detail": "Invalid or missing API key"}), "auth"),
    (httpx2.Response(404, json={"detail": "Flow identifier x not found"}), "flow_not_found"),
    (httpx2.Response(
        422, json={"detail": [{"loc": ["body", "flow_id"], "msg": "Field required"}]}
    ), "invalid_input"),
    (httpx2.Response(
        500, json={"detail": json.dumps({"message": "Error building Component Boom"})}
    ), "execution"),
    (httpx2.Response(502, text="bad gateway"), "bad_response"),
    (httpx2.Response(200, json=[1, 2]), "bad_response"),
    (httpx2.Response(200, json={"outputs": [{"outputs": [message("model failed", True)]}]}),
     "execution"),
])
async def test_errors_are_normalized(response: httpx2.Response, code: str) -> None:
    with pytest.raises(LangflowError) as exc:
        await client(Fake(run=response)).run("echo", "hi")
    assert exc.value.code == code
    assert not exc.value.retryable


@pytest.mark.asyncio
async def test_nested_500_message_is_extracted() -> None:
    body = {"detail": json.dumps({"message": "Error building Component Boom", "traceback": None})}
    with pytest.raises(LangflowError) as exc:
        await client(Fake(run=httpx2.Response(500, json=body))).run("echo", "hi")
    assert exc.value.message == "Error building Component Boom" and exc.value.status == 500


@pytest.mark.asyncio
@pytest.mark.parametrize(("failure", "code"), [
    (httpx2.ConnectError("refused"), "unavailable"),
    (httpx2.ReadTimeout("slow"), "timeout"),
])
async def test_transport_failures_are_retryable(failure: Exception, code: str) -> None:
    with pytest.raises(LangflowError) as exc:
        await client(Fake(run=failure)).run("echo", "hi")
    assert exc.value.code == code and exc.value.retryable


@pytest.mark.asyncio
async def test_circuit_breaker_opens_then_recovers() -> None:
    now = [0.0]
    fake = Fake(run=httpx2.ConnectError("refused"))
    c = LangflowClient(
        config(breaker_failures=2, breaker_seconds=30), transport=fake.transport(),
        clock=lambda: now[0],
    )
    for _ in range(2):
        with pytest.raises(LangflowError):
            await c.run("echo", "hi")
    calls = len(fake.calls)
    with pytest.raises(LangflowError, match="멈췄습니다"):
        await c.run("echo", "hi")
    assert len(fake.calls) == calls  # 열린 동안은 네트워크를 쓰지 않는다
    now[0] = 31
    fake.run = httpx2.Response(200, json=ok_body())
    assert (await c.run("echo", "hi")).text == "hello"


@pytest.mark.asyncio
@pytest.mark.parametrize(("version", "code"), [
    (httpx2.Response(200, json={**VERSION, "version": "1.12.8"}), "unsupported_version"),
    (httpx2.Response(200, json={**VERSION, "version": "2.0.0"}), "unsupported_version"),
    (httpx2.Response(200, json={"version": "1.13.0"}), "wrong_instance"),
    (httpx2.Response(200, text="<html>"), "wrong_instance"),
    (httpx2.Response(404, json={"detail": "Not Found"}), "wrong_instance"),
])
async def test_version_gate(version: httpx2.Response, code: str) -> None:
    fake = Fake(version=version)
    with pytest.raises(LangflowError) as exc:
        await client(fake).run("echo", "hi")
    assert exc.value.code == code
    assert all(call.url.path == "/api/v1/version" for call in fake.calls)


@pytest.mark.asyncio
async def test_version_is_checked_once_and_post_releases_pass() -> None:
    fake = Fake(version=httpx2.Response(200, json={**VERSION, "version": "1.13.0.post1"}))
    c = client(fake)
    await c.run("echo", "a")
    await c.run("echo", "b")
    assert [call.url.path for call in fake.calls].count("/api/v1/version") == 1


@pytest.mark.asyncio
async def test_output_is_truncated_and_multiple_outputs_joined() -> None:
    body = {"outputs": [{"outputs": [message("a" * 800), message("b" * 800)]}]}
    result = await client(Fake(run=httpx2.Response(200, json=body)), max_output_chars=1_000).run(
        "echo", "hi")
    assert result.truncated and len(result.text) == 1_000
    assert result.text.startswith("a" * 800 + "\n")


def test_config_rules(tmp_path: Path) -> None:
    for bad in [
        {"base_url": "http://example.com:7860"},
        {"base_url": "http://user:pw@127.0.0.1:7860"},
        {"base_url": "http://127.0.0.1:7860/api"},
        {"min_version": "2.0.0", "max_version": "1.0.0"},
        {"flow_descriptions": {"zzz": "x"}},
        {"flows": {"Bad Name": FLOW}},
        {"flows": {"echo": "not-a-uuid"}},
        {"unknown_key": 1},
    ]:
        with pytest.raises(ValidationError):
            LangflowConfig(**{"flows": {"echo": FLOW}, **bad})
    with pytest.raises(ValidationError):
        LangflowConfig(enabled=True)  # 허용 플로우 없이 켤 수 없다
    assert LangflowConfig().enabled is False
    LangflowConfig(base_url="https://langflow.internal", allow_remote=True)
    assert parse_version("1.13.0.post1") == (1, 13, 0)
    key = tmp_path / "key"
    key.write_text("sk-test\n")
    os.chmod(key, 0o644)
    with pytest.raises(OSError, match="권한"):
        config(api_key_file=key).read_api_key()
    os.chmod(key, stat.S_IRUSR | stat.S_IWUSR)
    assert config(api_key_file=key).read_api_key() == "sk-test"


@pytest.mark.asyncio
async def test_api_key_header_is_sent_but_never_in_errors(tmp_path: Path) -> None:
    key = tmp_path / "key"
    key.write_text("sk-secret-value\n")
    os.chmod(key, 0o600)
    fake = Fake(run=httpx2.Response(403, json={"detail": "Invalid API key"}))
    c = LangflowClient(config(api_key_file=key), transport=fake.transport())
    with pytest.raises(LangflowError) as exc:
        await c.run("echo", "hi")
    assert fake.calls[-1].headers["x-api-key"] == "sk-secret-value"
    assert "sk-secret-value" not in str(exc.value)


def ctx(tmp_path: Path, lf: LangflowClient | None) -> ToolContext:
    return ToolContext(cwd=tmp_path, sandbox=LocalBackend(), langflow=lf)


@pytest.mark.asyncio
async def test_tool_success_and_error_results(tmp_path: Path) -> None:
    tool = LangflowRunTool(config(flow_descriptions={"echo": "입력을 그대로 돌려줌"}))
    assert "echo: 입력을 그대로 돌려줌" in tool.description
    assert tool.input_schema["properties"]["flow"]["enum"] == ["echo"]
    assert not tool.is_read_only({}) and tool.kind == "execute"
    out = await tool.run(ctx(tmp_path, client(Fake())), {"flow": "echo", "input": "hi"})
    assert out.content == "hello" and not out.is_error
    down = client(Fake(run=httpx2.ConnectError("refused")))
    out = await tool.run(ctx(tmp_path, down), {"flow": "echo", "input": "hi"})
    assert out.is_error and "unavailable" in out.content and "다시 시도" in out.content
    out = await tool.run(ctx(tmp_path, client(Fake())), {"flow": "echo", "input": "hi", "x": 1})
    assert out.is_error  # 스키마 밖 필드
    out = await tool.run(ctx(tmp_path, None), {"flow": "echo", "input": "hi"})
    assert out.is_error and "꺼져" in out.content


# --- /flows, /flow 명령 -------------------------------------------------------------------------

from perdix.acp.flow_commands import flow_list, run_flow_command  # noqa: E402


@pytest.mark.asyncio
async def test_flow_commands_list_run_and_report_errors() -> None:
    cfg = config(flow_descriptions={"echo": "입력을 돌려줌"})
    listing = flow_list(cfg)
    assert "echo  입력을 돌려줌" in listing and "/flow <별칭> <입력>" in listing

    client_ok = LangflowClient(cfg, transport=Fake().transport())
    assert await run_flow_command("/flows", cfg, client_ok) == listing
    assert await run_flow_command("/flow", cfg, client_ok) == listing  # 인자 없으면 목록
    assert await run_flow_command("/flow echo", cfg, client_ok) == listing  # 입력 없으면 목록
    assert await run_flow_command("/flow echo 안녕 하세요", cfg, client_ok, "s1") == "hello\n"

    fake = Fake()
    guarded = LangflowClient(cfg, transport=fake.transport())
    out = await run_flow_command("/flow nope x", cfg, guarded)
    assert "unknown_flow" in out and fake.calls == []  # 허용 목록 밖은 네트워크에 가지 않는다

    down = LangflowClient(cfg, transport=Fake(run=httpx2.ConnectError("x")).transport())
    assert "unavailable" in await run_flow_command("/flow echo a", cfg, down)
    assert "꺼져" in await run_flow_command("/flow echo a", cfg, None)
