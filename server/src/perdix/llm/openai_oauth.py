from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import socket
import time
import uuid
import webbrowser
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import jwt
from pydantic import BaseModel, Field, ValidationError

from perdix.llm.base import ProviderError
from perdix.llm.openai_credentials import (
    OAuthRecord,
    TokenResponse,
    credential_lock,
    read_record,
    write_record,
)

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
TOKEN_ENDPOINT = ISSUER + "/api/accounts/oauth/token"
DIRECT_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke " + DIRECT_SCOPE


class Identity(BaseModel):
    sub: str = Field(min_length=1)
    nonce: str


class Discovery(BaseModel):
    revocation_endpoint: str


def auth_client() -> httpx2.AsyncClient:
    transport = httpx2.AsyncHTTPTransport(
        http2=True, retries=3,
        limits=httpx2.Limits(
            max_connections=200, max_keepalive_connections=40, keepalive_expiry=30,
        ),
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.AsyncClient(
        transport=transport,
        timeout=httpx2.Timeout(connect=5, read=30, write=10, pool=10),
        follow_redirects=False,
    )


async def post_token(client: httpx2.AsyncClient, data: dict[str, str]) -> TokenResponse:
    try:
        response = await client.post(TOKEN_ENDPOINT, data=data)
        response.raise_for_status()
        tokens = TokenResponse.model_validate_json(response.content)
    except (httpx2.HTTPError, ValidationError):
        raise ProviderError(
            "ChatGPT 토큰 발급/갱신에 실패했습니다. 다시 로그인해 주세요.", retryable=False
        ) from None
    if DIRECT_SCOPE not in tokens.scope.split():
        raise ProviderError("ChatGPT 플랜 사용 권한이 승인되지 않았습니다.", retryable=False)
    return tokens


async def verify_identity(
    client: httpx2.AsyncClient, token: str, client_id: str, nonce: str
) -> Identity:
    try:
        response = await client.get(ISSUER + "/.well-known/jwks.json")
        response.raise_for_status()
        keys = jwt.PyJWKSet.from_json(response.text)
        header = jwt.get_unverified_header(token)
        key = keys[header["kid"]]
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"], audience=client_id, issuer=ISSUER,
            options={"require": ["sub", "exp", "iat", "nonce"]}, leeway=5,
        )
        identity = Identity.model_validate(claims)
        if not secrets.compare_digest(identity.nonce.encode(), nonce.encode()):
            raise ProviderError("ChatGPT 로그인 nonce가 일치하지 않습니다.", retryable=False)
        return identity
    except (httpx2.HTTPError, jwt.PyJWTError, KeyError, ValidationError):
        raise ProviderError(
            "ChatGPT 로그인 ID 토큰 검증에 실패했습니다.", retryable=False
        ) from None


def token_record(
    previous: OAuthRecord, tokens: TokenResponse, *, now: float
) -> OAuthRecord:
    return previous.model_copy(update={
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "id_token": tokens.id_token or previous.id_token,
        "scopes": tuple(tokens.scope.split()),
        "expires_at": now + tokens.expires_in,
        "earliest_refresh_at": tokens.earliest_refresh_at,
    })


class OpenAiOAuth:
    def __init__(
        self, path: Path, *, open_browser: Callable[[str], bool] = webbrowser.open,
        client: httpx2.AsyncClient | None = None,
        login_timeout: float = 300,
    ) -> None:
        self.path = path
        self._open_browser = open_browser
        self._http = client
        self._login_timeout = login_timeout

    async def login(self) -> None:
        async with credential_lock(self.path):
            if self._http is not None:
                record = await self._login(self._http, read_record(self.path))
            else:
                async with auth_client() as client:
                    record = await self._login(client, read_record(self.path))
            write_record(self.path, record)

    async def logout(self) -> bool:
        async with credential_lock(self.path):
            record = read_record(self.path)
            if record is None:
                return True
            confirmed = True
            if record.refresh_token is not None:
                try:
                    if self._http is not None:
                        confirmed = await self._revoke(self._http, record)
                    else:
                        async with auth_client() as client:
                            confirmed = await self._revoke(client, record)
                except (httpx2.HTTPError, ValidationError):
                    confirmed = False
            write_record(self.path, record.model_copy(update={
                "access_token": None, "refresh_token": None, "id_token": None,
                "scopes": (), "expires_at": 0, "earliest_refresh_at": 0,
            }))
            return confirmed

    async def _revoke(self, client: httpx2.AsyncClient, record: OAuthRecord) -> bool:
        discovery = await client.get(ISSUER + "/.well-known/openid-configuration")
        discovery.raise_for_status()
        endpoint = Discovery.model_validate_json(discovery.content)
        if not endpoint.revocation_endpoint.startswith(ISSUER + "/"):
            return False
        assert record.refresh_token is not None
        response = await client.post(endpoint.revocation_endpoint, data={
            "token": record.refresh_token.get_secret_value(),
            "token_type_hint": "refresh_token", "client_id": record.client_id,
        })
        return response.status_code == 200

    async def access_token(self) -> str:
        async with credential_lock(self.path):
            record = read_record(self.path)
            if record and record.access_token and DIRECT_SCOPE not in record.scopes:
                raise ProviderError(
                    "ChatGPT 플랜 권한이 없습니다. 다시 로그인해 주세요.", retryable=False
                )
            now = time.time()
            if record and record.access_token and record.expires_at > now + 60:
                return record.access_token.get_secret_value()
            if self._http is not None:
                return await self._resolve(self._http, record, now)
            async with auth_client() as client:
                return await self._resolve(client, record, now)

    async def _resolve(
        self, client: httpx2.AsyncClient, record: OAuthRecord | None, now: float
    ) -> str:
        if record and record.refresh_token:
            if now < record.earliest_refresh_at and record.expires_at > now:
                if record.access_token is not None:
                    return record.access_token.get_secret_value()
            tokens = await post_token(client, {
                "grant_type": "refresh_token", "client_id": record.client_id,
                "refresh_token": record.refresh_token.get_secret_value(), "resource": RESOURCE,
            })
            updated = token_record(record, tokens, now=time.time())
        else:
            updated = await self._login(client, record)
        write_record(self.path, updated)
        assert updated.access_token is not None
        return updated.access_token.get_secret_value()

    async def _login(
        self, client: httpx2.AsyncClient, previous: OAuthRecord | None
    ) -> OAuthRecord:
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        future: asyncio.Future[dict[str, str]] = asyncio.get_running_loop().create_future()

        async def callback(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            status = "400 Bad Request"
            try:
                async with asyncio.timeout(5):
                    line = await reader.readline()
                    parts = line.decode("ascii").split()
                    if len(parts) == 3 and parts[0] == "GET":
                        url = urlsplit(parts[1])
                        query = parse_qs(url.query)
                        if (
                            url.path == "/auth/callback"
                            and all(len(v) == 1 for v in query.values())
                        ):
                            values = {k: v[0] for k, v in query.items()}
                            if secrets.compare_digest(
                                values.get("state", "").encode(), state.encode(),
                            ):
                                if not future.done():
                                    future.set_result(values)
                                    status = "200 OK"
                body = b"Return to Perdix to finish signing in."
                writer.write(
                    f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                )
                await writer.drain()
            except (TimeoutError, UnicodeError, ConnectionError, ValueError):
                writer.close()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(callback, "127.0.0.1", 0, limit=8192)
        async with server:
            port = server.sockets[0].getsockname()[1]
            redirect = f"http://127.0.0.1:{port}/auth/callback"
            host_path = self.path.with_name("openai-host-id")
            if previous:
                host = previous.ext_agent_host_id
            elif host_path.exists():
                host = host_path.read_text(encoding="utf-8")
            else:
                host = "urn:uuid:" + str(uuid.uuid4())
                fd = os.open(host_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as target:
                    target.write(host)
            params = {
                "client_id": previous.client_id if previous else "dynamic_agent_client",
                "ext_agent_host_id": host, "redirect_uri": redirect,
                "response_type": "code", "scope": SCOPES, "resource": RESOURCE,
                "state": state, "nonce": nonce, "code_challenge_method": "S256",
                "code_challenge": challenge.decode().rstrip("="),
            }
            if previous is None:
                params["agent_name_hint"] = "Perdix"
            if not self._open_browser(ISSUER + "/api/accounts/authorize?" + urlencode(params)):
                raise ProviderError(
                    "브라우저를 열 수 없습니다. 로컬 환경에서 "
                    "perdix auth login --provider openai를 실행해 주세요.", retryable=False,
                )
            try:
                async with asyncio.timeout(self._login_timeout):
                    values = await future
            except TimeoutError as exc:
                raise ProviderError(
                    "ChatGPT 로그인 시간이 초과되었습니다.", retryable=False
                ) from exc
        if "error" in values:
            raise ProviderError("ChatGPT 로그인이 취소되거나 거부되었습니다.", retryable=False)
        client_id = values.get("client_id", previous.client_id if previous else "")
        if not client_id or client_id == "dynamic_agent_client" or "code" not in values:
            raise ProviderError("ChatGPT 등록 콜백이 불완전합니다.", retryable=False)
        if previous and client_id != previous.client_id:
            raise ProviderError("ChatGPT 등록 client ID가 변경되었습니다.", retryable=False)
        tokens = await post_token(client, {
            "grant_type": "authorization_code", "client_id": client_id,
            "code": values["code"], "code_verifier": verifier,
            "redirect_uri": redirect, "resource": RESOURCE,
        })
        if tokens.id_token is None:
            raise ProviderError("ChatGPT ID 토큰이 없습니다.", retryable=False)
        identity = await verify_identity(
            client, tokens.id_token.get_secret_value(), client_id, nonce
        )
        if previous and identity.sub != previous.subject:
            raise ProviderError("ChatGPT 로그인 계정이 변경되었습니다.", retryable=False)
        registration = OAuthRecord(
            client_id=client_id, subject=identity.sub, ext_agent_host_id=host,
        )
        return token_record(registration, tokens, now=time.time())
