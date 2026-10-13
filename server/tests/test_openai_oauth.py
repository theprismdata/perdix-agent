from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
import jwt
import openai
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from perdix.config.credentials import load_credentials
from perdix.context.messages import AssistantMessage, TextBlock, ToolUseItem, UserMessage
from perdix.context.view import ModelView
from perdix.llm.base import ModelRequest, ProviderError, Stop, TextDelta, ToolSpec
from perdix.llm.openai import OpenAIProvider
from perdix.llm.openai_credentials import OAuthRecord, read_record, write_record
from perdix.llm.openai_oauth import ISSUER, RESOURCE, OpenAiOAuth


@pytest.fixture
def record() -> OAuthRecord:
    return OAuthRecord(
        client_id="oaiapp_test", subject="user-test", ext_agent_host_id="urn:uuid:test",
        access_token="access-old", refresh_token="refresh-old", id_token="id-old",
        scopes=("chatgpt.tokens.use.direct",), expires_at=time.time() + 3600,
    )


@dataclass(frozen=True, slots=True)
class SigningKey:
    private: rsa.RSAPrivateKey

    def id_token(self, audience: str, nonce: str, **overrides: str | int) -> str:
        claims: dict[str, str | int] = {
            "iss": ISSUER, "aud": audience, "sub": "user-test", "nonce": nonce,
            "iat": int(time.time()), "exp": int(time.time()) + 3600,
        }
        claims.update(overrides)
        return jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": "test"})

    def jwks(self) -> str:
        key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key()))
        key["kid"] = "test"
        return json.dumps({"keys": [key]})


@pytest.fixture
def signing_key() -> SigningKey:
    return SigningKey(rsa.generate_private_key(public_exponent=65537, key_size=2048))


def token_response(id_token: str | None = None) -> dict[str, str | int]:
    result: dict[str, str | int] = {
        "access_token": "access-new", "refresh_token": "refresh-new", "token_type": "Bearer",
        "expires_in": 3600, "scope": "openid offline_access chatgpt.tokens.use.direct",
    }
    if id_token:
        result["id_token"] = id_token
    return result


@pytest.mark.asyncio
async def test_saved_token_does_not_open_browser(tmp_path: Path, record: OAuthRecord) -> None:
    path = tmp_path / "openai-oauth.json"
    write_record(path, record)

    def browser(url: str) -> bool:
        pytest.fail("saved tokens must not start a browser")

    token = await OpenAiOAuth(path, open_browser=browser).access_token()

    assert token == "access-old"
    assert "access-old" not in repr(read_record(path))
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_expired_token_refresh_is_serialized(
    tmp_path: Path, record: OAuthRecord,
) -> None:
    path = tmp_path / "openai-oauth.json"
    write_record(path, record.model_copy(update={"expires_at": 0}))
    calls: list[dict[str, list[str]]] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(parse_qs(request.content.decode()))
        await asyncio.sleep(0)
        return httpx2.Response(200, json=token_response())

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        results = await asyncio.gather(*(
            OpenAiOAuth(path, client=client).access_token() for _ in range(3)
        ))

    assert results == ["access-new"] * 3
    assert calls == [{
        "grant_type": ["refresh_token"], "client_id": ["oaiapp_test"],
        "refresh_token": ["refresh-old"], "resource": [RESOURCE],
    }]
    saved = read_record(path)
    assert saved is not None and saved.refresh_token is not None
    assert saved.refresh_token.get_secret_value() == "refresh-new"
    assert saved.expires_at > time.time() + 3500


@pytest.mark.asyncio
async def test_failed_refresh_preserves_previous_tokens(
    tmp_path: Path, record: OAuthRecord,
) -> None:
    path = tmp_path / "openai-oauth.json"
    expired = record.model_copy(update={"expires_at": 0})
    write_record(path, expired)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(
        lambda request: httpx2.Response(400, json={"error": "invalid_grant"})
    )) as client:
        with pytest.raises(ProviderError):
            await OpenAiOAuth(path, client=client).access_token()
    assert read_record(path) == expired


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, "nonce", "audience", "signature", "scope"])
async def test_loopback_login_validates_identity_before_saving(
    tmp_path: Path, signing_key: SigningKey, invalid: str | None,
) -> None:
    path = tmp_path / "openai-oauth.json"
    auth: dict[str, list[str]] = {}
    callback_tasks: list[asyncio.Task[None]] = []

    async def callback() -> None:
        redirect = auth["redirect_uri"][0]
        async with httpx2.AsyncClient(trust_env=False) as browser_client:
            wrong = await browser_client.get(redirect, params={"state": "wrong", "code": "bad"})
            assert wrong.status_code == 400
            result = await browser_client.get(redirect, params={
                "state": auth["state"][0], "code": "code-test", "client_id": "oaiapp_test",
            })
            assert result.status_code == 200

    def browser(url: str) -> bool:
        auth.update(parse_qs(urlsplit(url).query))
        callback_tasks.append(asyncio.create_task(callback()))
        return True

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/.well-known/jwks.json":
            return httpx2.Response(200, text=signing_key.jwks())
        form = parse_qs(request.content.decode())
        assert form["client_id"] == ["oaiapp_test"]
        assert form["code"] == ["code-test"]
        assert form["redirect_uri"] == auth["redirect_uri"]
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(form["code_verifier"][0].encode()).digest()
        ).decode().rstrip("=")
        assert challenge == auth["code_challenge"][0]
        nonce = "wrong" if invalid == "nonce" else auth["nonce"][0]
        audience = "wrong" if invalid == "audience" else "oaiapp_test"
        signer = signing_key if invalid != "signature" else SigningKey(
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
        )
        tokens = token_response(signer.id_token(audience, nonce))
        if invalid == "scope":
            tokens["scope"] = "openid"
        return httpx2.Response(200, json=tokens)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        oauth = OpenAiOAuth(path, client=client, open_browser=browser)
        if invalid:
            with pytest.raises(ProviderError):
                await oauth.access_token()
            assert not path.exists()
        else:
            assert await oauth.access_token() == "access-new"
        await asyncio.gather(*callback_tasks)
    assert auth["client_id"] == ["dynamic_agent_client"]
    assert auth["agent_name_hint"] == ["Perdix"]
    assert (tmp_path / "openai-host-id").read_text() == auth["ext_agent_host_id"][0]


@pytest.mark.asyncio
async def test_cancel_closes_listener_and_does_not_save_tokens(tmp_path: Path) -> None:
    opened = asyncio.Event()
    urls: list[str] = []

    def browser(url: str) -> bool:
        urls.append(url)
        opened.set()
        return True

    task = asyncio.create_task(OpenAiOAuth(
        tmp_path / "openai-oauth.json", open_browser=browser,
    ).access_token())
    await asyncio.wait_for(opened.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    callback = parse_qs(urlsplit(urls[0]).query)["redirect_uri"][0]
    async with httpx2.AsyncClient(trust_env=False) as client:
        with pytest.raises(httpx2.ConnectError):
            await client.get(callback)
    assert not (tmp_path / "openai-oauth.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("browser_available", [False, True])
async def test_browser_failure_or_timeout_leaves_no_tokens(
    tmp_path: Path, browser_available: bool,
) -> None:
    path = tmp_path / "openai-oauth.json"
    with pytest.raises(ProviderError):
        await OpenAiOAuth(
            path, open_browser=lambda url: browser_available, login_timeout=0,
        ).access_token()
    assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 503])
async def test_logout_clears_tokens_and_retains_registration(
    tmp_path: Path, record: OAuthRecord, status: int,
) -> None:
    path = tmp_path / "openai-oauth.json"
    write_record(path, record)
    calls: list[dict[str, list[str]]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET":
            return httpx2.Response(200, json={
                "revocation_endpoint": ISSUER + "/api/accounts/oauth/revoke",
            })
        calls.append(parse_qs(request.content.decode()))
        return httpx2.Response(status)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        confirmed = await OpenAiOAuth(path, client=client).logout()
    assert confirmed is (status == 200)
    saved = read_record(path)
    assert saved is not None and saved.client_id == record.client_id
    assert saved.subject == record.subject and saved.ext_agent_host_id == record.ext_agent_host_id
    assert saved.access_token is None and saved.refresh_token is None and saved.id_token is None
    assert calls == [{
        "token": ["refresh-old"], "token_type_hint": ["refresh_token"],
        "client_id": ["oaiapp_test"],
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize("key_source", ["env", "file", "empty", "none"])
async def test_provider_key_precedence_and_oauth_request_shape(
    tmp_path: Path, record: OAuthRecord, monkeypatch: pytest.MonkeyPatch, key_source: str,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    credentials = tmp_path / "credentials.json"
    if key_source == "env":
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        credentials.write_text(json.dumps({"openai": {"api_key": "file-key"}}))
    elif key_source == "file":
        credentials.write_text(json.dumps({"openai": {"api_key": "file-key"}}))
    elif key_source == "empty":
        credentials.write_text(json.dumps({"openai": {"api_key": ""}}))
    write_record(tmp_path / "openai-oauth.json", record)
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        events = [
            {"type": "response.output_text.delta", "delta": "ok", "item_id": "msg_1",
             "output_index": 0, "content_index": 0, "sequence_number": 0},
            {"type": "response.completed", "sequence_number": 1, "response": {
                "id": "resp_1", "object": "response", "created_at": 0, "status": "completed",
                "model": "gpt-6.1-sol", "output": [], "tools": [], "tool_choice": "auto",
                "parallel_tool_calls": True,
            }},
        ]
        body = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
        return httpx2.Response(200, text=body, headers={"content-type": "text/event-stream"})

    constructor = openai.AsyncOpenAI

    def make_client(*, api_key: str, base_url: str | None, max_retries: int) -> openai.AsyncOpenAI:
        return constructor(api_key=api_key, base_url=base_url, max_retries=max_retries,
                           http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))

    monkeypatch.setattr(openai, "AsyncOpenAI", make_client)
    request = ModelRequest(
        system="system", max_tokens=100, view=ModelView(messages=[
            UserMessage(content=[TextBlock(text="hello")]),
            AssistantMessage(
                provider="openai", model="gpt-6.1-sol", stop_reason="tool_use",
                items=[ToolUseItem(id="call_1", name="read", input={})],
            ),
        ]), tools=[ToolSpec(name="read", description="read", input_schema={"type": "object"})],
    )

    events = [event async for event in OpenAIProvider(
        "gpt-6.1-sol", credentials_path=credentials,
    ).stream(request)]

    assert any(isinstance(event, TextDelta) and event.text == "ok" for event in events)
    assert isinstance(events[-1], Stop)
    sent = json.loads(requests[0].content)
    expected_key = {"env": "env-key", "file": "file-key"}.get(key_source, "access-old")
    assert requests[0].headers["authorization"] == "Bearer " + expected_key
    assert sent["stream"] is True and sent["store"] is False
    if key_source in {"env", "file"}:
        assert sent["max_output_tokens"] == 100
        assert sent["tools"][0]["type"] == "function"
        assert "namespace" not in sent["input"][1]
    else:
        assert "max_output_tokens" not in sent
        assert sent["tools"][0]["type"] == "namespace"
        assert sent["tools"][0]["description"]
        assert sent["tools"][0]["tools"][0]["description"] == "read"
        assert sent["input"][1]["namespace"] == "perdix"
        assert requests[0].url == httpx2.URL(RESOURCE + "/responses")


def test_empty_key_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text('{"openai":{"api_key":""}}')
    assert not load_credentials(path).api_key("openai", env={})


@pytest.mark.asyncio
async def test_invalid_api_key_does_not_fall_back_to_oauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "invalid-key")
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(401, json={"error": {"message": "invalid API key"}})

    constructor = openai.AsyncOpenAI

    def make_client(*, api_key: str, base_url: str | None, max_retries: int) -> openai.AsyncOpenAI:
        return constructor(api_key=api_key, base_url=base_url, max_retries=max_retries,
                           http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))

    monkeypatch.setattr(openai, "AsyncOpenAI", make_client)
    request = ModelRequest(system="s", max_tokens=10, view=ModelView(messages=[]))

    with pytest.raises(ProviderError) as failure:
        async for _ in OpenAIProvider(
            "gpt-6.1-sol", credentials_path=tmp_path / "credentials.json",
        ).stream(request):
            pass

    assert failure.value.status == 401
    assert len(calls) == 1
    assert not (tmp_path / "openai-host-id").exists()
