import asyncio
import hmac
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import httpx2
import truststore
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from perdix.gateway.channels import GatewayChannels, GitHubImport, GitHubWebhook
from perdix.gateway.config import LOOPBACK_HOSTS, GatewayConfig, GatewaySecrets
from perdix.gateway.events import GatewayStore, Job, JobEvent
from perdix.gateway.runtime import GatewayRuntime, SubmitRequest
from perdix.gateway.workflows import workflow_router

STATIC = Path(__file__).parent / "static"


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_id: str | None = Field(alias="optionId", default=None)


def create_app(
    config: GatewayConfig,
    secrets: GatewaySecrets,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> FastAPI:
    store = GatewayStore(config.db_path)
    runtime = GatewayRuntime(config, store)
    http = httpx2.AsyncClient(
        verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        http2=True,
        timeout=httpx2.Timeout(35, connect=5),
        transport=transport,
        follow_redirects=False,
        trust_env=False,
    )
    channels = GatewayChannels(runtime, secrets, http)
    runtime.on_finished = channels.finished

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await runtime.start()
        polling = asyncio.create_task(channels.poll_telegram()) if secrets.telegram else None
        try:
            yield
        finally:
            if polling:
                polling.cancel()
                await asyncio.gather(polling, return_exceptions=True)
            await runtime.close()
            await http.aclose()

    app = FastAPI(
        title="Perdix Gateway", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.runtime = runtime
    app.state.channels = channels
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[config.host, "localhost", "127.0.0.1", "[::1]", "testserver"],
    )

    async def authorize(
        request: Request, authorization: Annotated[str | None, Header()] = None
    ) -> None:
        if secrets.gateway is None:
            # 토큰 없는 로컬 모드: 다른 사이트가 보낸 요청은 사용자 정의 헤더(사전 요청 필요)와
            # Origin 검사로 막는다. DNS rebinding은 TrustedHostMiddleware가 막는다.
            origin = request.headers.get("origin")
            if request.headers.get("x-perdix-client") != "dashboard" or (
                origin and urlsplit(origin).hostname not in {*LOOPBACK_HOSTS, "[::1]", "testserver"}
            ):
                raise HTTPException(403, "이 컴퓨터의 Perdix 대시보드에서만 접속할 수 있습니다")
            return
        expected = "Bearer " + secrets.gateway.get_secret_value()
        if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(401, "게이트웨이 연결 토큰을 확인하세요")

    auth = [Depends(authorize)]
    app.include_router(workflow_router(runtime), dependencies=auth)

    @app.middleware("http")
    async def security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    async def dashboard() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/dashboard.css")
    async def stylesheet() -> FileResponse:
        return FileResponse(STATIC / "dashboard.css", media_type="text/css")

    @app.get("/favicon.svg")
    async def favicon() -> FileResponse:
        return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")

    @app.get("/dashboard.js")
    async def javascript() -> FileResponse:
        return FileResponse(STATIC / "dashboard.js", media_type="text/javascript")

    @app.get("/graph.js")
    async def graph_script() -> FileResponse:
        return FileResponse(STATIC / "graph.js", media_type="text/javascript")

    @app.get("/graph.css")
    async def graph_style() -> FileResponse:
        return FileResponse(STATIC / "graph.css", media_type="text/css")

    @app.get("/workflow-editor.js")
    @app.get("/draft-plan.js")
    @app.get("/draft-canvas.js")
    @app.get("/editor.css")
    async def editor_asset(request: Request) -> FileResponse:
        name = request.url.path.rsplit("/", 1)[-1]
        media_type = "text/css" if name == "editor.css" else "text/javascript"
        return FileResponse(STATIC / name, media_type=media_type)

    @app.get("/api/status", dependencies=auth)
    async def status() -> dict[str, object]:
        return {
            "projects": [
                {"name": name, "githubRepository": project.github_repository}
                for name, project in config.projects.items()
            ],
            "channels": channels.status.model_dump(),
            "activeJobs": len(runtime.connections),
            "workflowDefaultModel": config.server.model,
        }

    @app.get("/api/jobs", dependencies=auth)
    async def jobs() -> list[Job]:
        return await store.jobs()

    @app.post("/api/jobs", dependencies=auth, status_code=202)
    async def submit(request: SubmitRequest) -> Job:
        try:
            return await runtime.submit(request)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    async def require_job(job_id: str) -> Job:
        job = await store.get(job_id)
        if not job:
            raise HTTPException(404, "작업을 찾을 수 없습니다")
        return job

    @app.get("/api/jobs/{job_id}/events", dependencies=auth)
    async def events(job_id: str, after: Annotated[int, Query(ge=0)] = 0) -> list[JobEvent]:
        await require_job(job_id)
        result = await store.events(job_id, after)
        return [
            event.model_copy(
                update={"data": {to_camel(key): value for key, value in event.data.items()}}
            )
            if event.kind in ("submitted", "state") and isinstance(event.data, dict)
            else event
            for event in result
        ]

    @app.post("/api/jobs/{job_id}/cancel", dependencies=auth)
    async def cancel(job_id: str) -> Job:
        await require_job(job_id)
        await runtime.cancel(job_id)
        return await require_job(job_id)

    @app.get("/api/jobs/{job_id}/permissions", dependencies=auth)
    async def permissions(job_id: str) -> list[dict[str, object]]:
        await require_job(job_id)
        return [
            {"permissionId": key, "options": [o.model_dump(by_alias=True) for o in pending.options]}
            for key, pending in runtime.permissions.items()
            if pending.job_id == job_id
        ]

    @app.post("/api/jobs/{job_id}/permissions/{permission_id}", dependencies=auth)
    async def approve(job_id: str, permission_id: str, request: ApprovalRequest) -> dict[str, bool]:
        try:
            runtime.approve(job_id, permission_id, request.option_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return {"accepted": True}

    @app.post("/api/github/import", dependencies=auth, status_code=202)
    async def github_import(request: GitHubImport) -> Job:
        try:
            return await channels.import_issue(request)
        except (httpx2.HTTPError, ValidationError):
            raise HTTPException(
                502, "GitHub 이슈를 가져오지 못했습니다. 저장소·번호·토큰을 확인하세요."
            ) from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    @app.post("/webhooks/github")
    async def github_webhook(
        request: Request,
        x_hub_signature_256: Annotated[str, Header()] = "",
        x_github_event: Annotated[str, Header()] = "",
    ) -> JSONResponse:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 262144:
                raise HTTPException(413, "Webhook 본문이 너무 큽니다")
        if not channels.verify_signature(bytes(body), x_hub_signature_256):
            raise HTTPException(401, "Webhook 서명이 유효하지 않습니다")
        if x_github_event != "issue_comment":
            return JSONResponse({"accepted": False})
        try:
            event = GitHubWebhook.model_validate_json(body)
            job = await channels.webhook(event)
        except ValidationError:
            raise HTTPException(400, "Webhook 형식이 유효하지 않습니다") from None
        except httpx2.HTTPError:
            raise HTTPException(502, "GitHub 이슈 조회에 실패했습니다") from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        return JSONResponse(
            {"accepted": job is not None, "jobId": job.id if job else None},
            status_code=202 if job else 200,
        )

    return app
