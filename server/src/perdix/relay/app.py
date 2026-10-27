"""OpenAI 호환 `/v1/chat/completions` 중계 서버(ADR 0027).

공급자 호출은 기존 `Provider` 어댑터가 하므로 OAuth 등 Perdix의 인증을 그대로 쓴다.
외부 도구(Langflow)는 URL과 중계 토큰만 안다. 공급자 키는 이 프로세스 밖으로 나가지 않는다.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from perdix.config.agents import AgentsConfig, load_agents_config
from perdix.config.credentials import DEFAULT_CREDENTIALS_PATH, Credentials, load_credentials
from perdix.llm import catalog
from perdix.llm.base import Provider, ProviderError
from perdix.llm.factory import build_provider
from perdix.permissions.policy import PermissionMode
from perdix.relay.roles import Roles
from perdix.relay.tools_mcp import ToolsMcp
from perdix.relay.wire import to_chunks, to_completion, to_request

logger = logging.getLogger(__name__)
DEFAULT_TOKEN_PATH = Path.home() / ".perdix" / "relay-token"


def load_or_create_token(path: Path) -> str:
    """중계 토큰 파일(0600). 없으면 만든다."""
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.touch(mode=0o600)
    path.write_text(token + "\n", encoding="utf-8")
    return token


# langchain은 gpt-5 이상 이름을 보면 Responses API로 보낸다. 접두사로 그 휴리스틱을 피한다.
MODEL_PREFIX = "perdix:"


ROLE_PREFIX = "role/"  # perdix:role/<역할> → 그 역할에 할당된 모델


def provider_name_for(model: str, agents: AgentsConfig | None = None) -> str | None:
    assigned = agents.provider_for(model) if agents is not None else None
    if assigned is not None:
        return assigned
    try:
        return catalog.get(model).provider
    except KeyError:
        return "openrouter" if "/" in model else None


def _error(status: int, message: str, kind: str = "perdix_relay_error") -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)


def create_app(
    token: str,
    *,
    credentials: Credentials | None = None,
    credentials_path: Path = DEFAULT_CREDENTIALS_PATH,
    providers: dict[str, Provider] | None = None,
    workspace: Path | None = None,
    agents_config: AgentsConfig | None = None,
    permission_mode: PermissionMode = "deny",
) -> FastAPI:
    """`providers`는 테스트용 모델명 → Provider 주입 지점이다."""
    app = FastAPI(title="perdix-relay")
    creds = credentials if credentials is not None else load_credentials(credentials_path)
    cache: dict[str, Provider] = dict(providers or {})
    agents = agents_config if agents_config is not None else load_agents_config(None)
    roles = Roles(workspace, agents) if workspace is not None else None

    def authorize(authorization: str | None) -> None:
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "invalid relay token")

    def provider_for(model: str) -> tuple[Provider, str] | None:
        if model in cache:
            return cache[model], "injected"
        name = provider_name_for(model, agents)
        if name is None:
            return None
        cache[model] = build_provider(
            name, model, credentials=creds, credentials_path=credentials_path)
        return cache[model], name

    if roles is not None:
        mcp = ToolsMcp(roles, permission_mode)

        async def serve_mcp(role: str, request: Request, authorization: str | None) -> Any:
            authorize(authorization)
            if not roles.known(role):
                return _error(404, f"unknown role: {role}", "invalid_request_error")
            reply = await mcp.handle(role, await request.json())
            return Response(status_code=202) if reply is None else JSONResponse(reply)

        @app.post("/mcp/{role}", response_model=None)
        async def mcp_role(
            role: str, request: Request, authorization: str | None = Header(default=None)
        ) -> Any:
            return await serve_mcp(role, request, authorization)

        @app.post("/mcp", response_model=None)
        async def mcp_default(
            request: Request, authorization: str | None = Header(default=None)
        ) -> Any:
            return await serve_mcp("chat", request, authorization)

        @app.get("/v1/agents")
        async def list_agents(authorization: str | None = Header(default=None)) -> Any:
            authorize(authorization)
            return {"object": "list", "data": [
                {"role": r, "model": roles.model_and_effort(r)[0]} for r in roles.names()]}

        @app.get("/v1/agents/{role}", response_model=None)
        async def get_agent(role: str, authorization: str | None = Header(default=None)) -> Any:
            authorize(authorization)
            if not roles.known(role):
                return _error(404, f"unknown role: {role}", "invalid_request_error")
            return roles.definition(role).model_dump()

    @app.get("/v1/models")
    async def models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        authorize(authorization)
        return {"object": "list", "data": [
            {"id": m, "object": "model", "owned_by": catalog.get(m).provider}
            for m in (i.id for i in catalog.all_models())
        ]}

    @app.post("/v1/chat/completions", response_model=None)
    async def completions(
        request: Request, authorization: str | None = Header(default=None)
    ) -> Any:
        authorize(authorization)
        body = await request.json()
        model = str(body.get("model", "")).removeprefix(MODEL_PREFIX)
        if model.startswith(ROLE_PREFIX):
            if roles is None or not roles.known(model.removeprefix(ROLE_PREFIX)):
                return _error(400, f"unknown role: {model}", "invalid_request_error")
            model = roles.model_and_effort(model.removeprefix(ROLE_PREFIX))[0]
        found = provider_for(model)
        if found is None:
            return _error(400, f"unknown model: {model}", "invalid_request_error")
        provider, provider_name = found
        model_request = to_request(body, provider, provider_name=provider_name)
        logger.info("relay %s: %d messages, tools=%s", model, len(model_request.view.messages),
                    [t.name for t in model_request.tools])
        chunks = to_chunks(provider.stream(model_request),
                           id_=f"chatcmpl-{uuid.uuid4().hex[:24]}",
                           model=str(body.get("model", "")))
        try:
            # 공급자 연결 오류를 스트림 시작 전에 드러낸다.
            head = [await anext(chunks), await anext(chunks, None)]
        except ProviderError as exc:
            return _error(exc.status or 502, str(exc), "provider_error")

        async def rest() -> AsyncIterator[dict[str, Any]]:
            for chunk in head:
                if chunk is not None:
                    yield chunk
            try:
                async for chunk in chunks:
                    yield chunk
            except ProviderError as exc:
                yield {"error": {"message": str(exc), "type": "provider_error"}}

        if not body.get("stream"):
            collected = [c async for c in rest()]
            if "error" in collected[-1]:
                return _error(502, collected[-1]["error"]["message"], "provider_error")
            return JSONResponse(to_completion(collected))
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

        async def sse() -> AsyncIterator[str]:
            async for chunk in rest():
                if "usage" in chunk and not include_usage:
                    continue
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")

    return app
