import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from acp import PROTOCOL_VERSION, Client, text_block
from acp.interfaces import Agent
from acp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    ClientCapabilities,
    DeniedOutcome,
    PermissionOption,
    RequestPermissionResponse,
    ToolCallUpdate,
)
from acp.stdio import spawn_agent_process
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from perdix.gateway.config import GatewayConfig
from perdix.gateway.events import GatewayStore, Job
from perdix.llm import catalog
from perdix.sandbox.base import scrub_env

logger = logging.getLogger(__name__)


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=32000)


@dataclass
class PendingPermission:
    job_id: str
    options: list[PermissionOption]
    future: asyncio.Future[str | None]


class JobClient(Client):
    def __init__(self, gateway: "GatewayRuntime", job_id: str) -> None:
        self.gateway = gateway
        self.job_id = job_id
        self.text: list[str] = []

    def on_connect(self, conn: Agent) -> None:
        return None

    async def write_text_file(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("fs capability is not advertised")

    async def read_text_file(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("fs capability is not advertised")

    async def create_terminal(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("terminal capability is not advertised")

    async def terminal_output(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("terminal capability is not advertised")

    async def release_terminal(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("terminal capability is not advertised")

    async def wait_for_terminal_exit(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("terminal capability is not advertised")

    async def kill_terminal(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("terminal capability is not advertised")

    async def create_elicitation(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("elicitation capability is not advertised")

    async def complete_elicitation(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        data: JsonValue = json.loads(update.model_dump_json(by_alias=True, exclude_none=True))
        await self.gateway.store.append(self.job_id, "update", data)
        if isinstance(update, AgentMessageChunk) and update.content.type == "text":
            self.text.append(update.content.text)

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        permission_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        pending = PendingPermission(self.job_id, options, future)
        self.gateway.permissions[permission_id] = pending
        await self.gateway.store.append(
            self.job_id,
            "permission",
            {
                "permissionId": permission_id,
                "toolCall": json.loads(tool_call.model_dump_json(by_alias=True, exclude_none=True)),
                "options": [json.loads(o.model_dump_json(by_alias=True)) for o in options],
            },
        )
        await self.gateway.store.append(self.job_id, "state", {"status": "waiting"})
        try:
            async with asyncio.timeout(600):
                selected = await future
        except TimeoutError:
            selected = None
        finally:
            self.gateway.permissions.pop(permission_id, None)
        await self.gateway.store.append(
            self.job_id,
            "permissionResolved",
            {
                "permissionId": permission_id,
            },
        )
        await self.gateway.store.append(self.job_id, "state", {"status": "running"})
        if selected is None:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id=selected)
        )


class GatewayRuntime:
    def __init__(self, config: GatewayConfig, store: GatewayStore) -> None:
        self.config = config
        self.store = store
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.connections: dict[str, tuple[Agent, str]] = {}
        self.permissions: dict[str, PendingPermission] = {}
        self.limit = asyncio.Semaphore(config.max_concurrent_jobs)
        self.project_locks = {name: asyncio.Lock() for name in config.projects}
        self.submit_lock = asyncio.Lock()
        self.stopping = False
        self.on_finished: Callable[[Job], Awaitable[None]] | None = None

    async def start(self) -> None:
        await self.store.open()
        assert self.store.db is not None
        async with self.store.db.execute(
            "SELECT job_id FROM gateway_events WHERE kind='submitted'",
        ) as cursor:
            ids = await cursor.fetchall()
        for row in ids:
            job = await self.store.get(row[0])
            if job and job.status in ("queued", "running", "waiting"):
                await self.store.append(
                    job.id,
                    "state",
                    {
                        "status": "interrupted",
                        "result": "게이트웨이가 재시작되어 작업이 중단되었습니다.",
                    },
                )

    async def submit(
        self,
        request: SubmitRequest,
        source: str = "web",
        source_id: str | None = None,
    ) -> Job:
        if request.project not in self.config.projects:
            raise ValueError("등록되지 않은 프로젝트입니다")
        if not request.prompt.strip():
            raise ValueError("작업 내용을 입력하세요")
        async with self.submit_lock:
            if self.stopping:
                raise ValueError("게이트웨이가 종료 중입니다")
            if source_id:
                existing = await self.store.source_job(source_id)
                if existing:
                    job = await self.store.get(existing)
                    assert job is not None
                    return job
            if len(self.tasks) >= 100:
                raise ValueError("작업 대기열이 가득 찼습니다")
            project = self.config.projects[request.project]
            agent_identity = project.acp.identity if project.acp else "perdix"
            selected_model = None
            if source == "telegram" and source_id and not project.acp:
                conversation = source_id.rsplit(":", 1)[0]
                selected_model = await self.store.selected_model(
                    f"{conversation}:{request.project}:{agent_identity}",
                )
            job = Job(
                id=str(uuid4()),
                project=request.project,
                prompt=request.prompt,
                source=source,
                source_id=source_id,
                created_at=time.time(),
                agent=agent_identity,
                model=selected_model,
            )
            await self.store.append(job.id, "submitted", job.model_dump(mode="json"), source_id)
            self.tasks[job.id] = asyncio.create_task(self._run(job), name=f"gateway-job-{job.id}")
            return job

    async def _run(self, job: Job) -> None:
        client = JobClient(self, job.id)
        try:
            async with self.project_locks[job.project], self.limit:
                if not job.session_id and job.source == "telegram" and job.source_id:
                    conversation = job.source_id.rsplit(":", 1)[0] + ":"
                    previous = next((
                        item for item in await self.store.jobs()
                        if item.id != job.id and item.source == "telegram"
                        and item.project == job.project and item.source_id
                        and item.agent == job.agent
                        and item.source_id.startswith(conversation) and item.session_id
                    ), None)
                    if previous is not None:
                        job = job.model_copy(update={"session_id": previous.session_id})
                project = self.config.projects[job.project]
                server = self.config.server
                model = job.model or server.model
                provider = catalog.get(model).provider if job.model else server.provider
                if server.provider == "fake":
                    provider = "fake"
                args = [
                    "-m",
                    "perdix",
                    "--provider",
                    provider,
                    "--model",
                    model,
                    "--db",
                    str(os.path.abspath(server.db_path)),
                    "--credentials",
                    str(server.credentials_path.resolve()),
                    "--permission-mode",
                    server.permission_mode,
                    "--log-level",
                    "WARNING",
                ]
                if server.fake_script:
                    args.extend(["--fake-script", str(server.fake_script.resolve())])
                args.extend(["--max-tokens", str(server.max_tokens)])
                if server.effort:
                    args.extend(["--effort", server.effort])
                if server.agents_config_path:
                    args.extend(["--agents-config", str(server.agents_config_path.resolve())])
                if project.trust_project:
                    args.append("--trust-project")
                command = sys.executable
                mode: str | None = "build"
                if project.acp:
                    command, *args = project.acp.command
                    mode = project.acp.mode
                env = scrub_env(os.environ)
                for key in (
                    self.config.token_env,
                    self.config.telegram.token_env if self.config.telegram else "",
                    self.config.github.token_env if self.config.github else "",
                    self.config.github.webhook_secret_env if self.config.github else "",
                ):
                    env.pop(key, None)
                async with AsyncExitStack() as process:
                    conn: Agent
                    conn, _ = await process.enter_async_context(spawn_agent_process(
                        client, command, *args, env=env, cwd=project.cwd,
                        transport_kwargs={"stderr": None}, use_unstable_protocol=True,
                    ))
                    async with asyncio.timeout(30):
                        initialized = await conn.initialize(
                            protocol_version=PROTOCOL_VERSION,
                            client_capabilities=ClientCapabilities(),
                        )
                        capabilities = initialized.agent_capabilities
                        if job.session_id and capabilities and capabilities.load_session:
                            await conn.load_session(session_id=job.session_id,
                                                    cwd=str(project.cwd), mcp_servers=[])
                            session_id = job.session_id
                        else:
                            session = await conn.new_session(cwd=str(project.cwd), mcp_servers=[])
                            session_id = session.session_id
                            if mode is not None:
                                await conn.set_session_mode(session_id=session_id, mode_id=mode)
                    self.connections[job.id] = (conn, session_id)
                    if job.model and not project.acp:
                        await conn.set_config_option(
                            config_id="model", session_id=session_id, value=job.model,
                        )
                    await self.store.append(
                        job.id,
                        "state",
                        {
                            "status": "running",
                            "session_id": session_id,
                        },
                    )
                    client.text.clear()
                    response = await conn.prompt(
                        session_id=session_id, prompt=[text_block(job.prompt)]
                    )
                    status = "completed" if response.stop_reason == "end_turn" else "failed"
                    metadata = (response.field_meta or {}).get("perdix", {})
                    if isinstance(metadata, dict) and metadata.get("verification") == "failed":
                        status = "failed"
                    if response.stop_reason == "cancelled":
                        status = "cancelled"
                    await self.store.append(
                        job.id,
                        "state",
                        {
                            "status": status,
                            "session_id": session_id,
                            "result": "".join(client.text) or f"종료 사유: {response.stop_reason}",
                        },
                    )
        except asyncio.CancelledError:
            await self.store.append(
                job.id, "state", {"status": "cancelled", "result": "작업을 취소했습니다."}
            )
            raise
        except Exception as error:
            # 작업 경계에서 실패를 기록한다. 공급자 예외 본문에는 비밀이 있을 수 있다.
            logger.error("gateway job failed: %s (%s)", job.id, type(error).__name__)
            await self.store.append(
                job.id,
                "state",
                {
                    "status": "failed",
                    "result": "작업 실행에 실패했습니다. 서버 설정과 자격 증명을 확인하세요.",
                },
            )
        finally:
            self.connections.pop(job.id, None)
            for permission_id, pending in list(self.permissions.items()):
                if pending.job_id == job.id:
                    if not pending.future.done():
                        pending.future.set_result(None)
                    self.permissions.pop(permission_id, None)
            try:
                finished = await self.store.get(job.id)
                if finished and self.on_finished and not self.stopping:
                    await self.on_finished(finished)
            finally:
                self.tasks.pop(job.id, None)

    async def cancel(self, job_id: str) -> None:
        task = self.tasks.get(job_id)
        if task:
            connection = self.connections.get(job_id)
            if connection:
                conn, session_id = connection
                await conn.cancel(session_id=session_id)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.tasks.pop(job_id, None)
            job = await self.store.get(job_id)
            if job and job.status in ("queued", "running", "waiting"):
                await self.store.append(
                    job_id,
                    "state",
                    {
                        "status": "cancelled",
                        "result": "작업을 취소했습니다.",
                    },
                )

    def approve(self, job_id: str, permission_id: str, option_id: str | None) -> None:
        pending = self.permissions.get(permission_id)
        if not pending or pending.job_id != job_id or pending.future.done():
            raise ValueError("이미 처리되었거나 만료된 승인 요청입니다")
        if option_id is not None and option_id not in {o.option_id for o in pending.options}:
            raise ValueError("유효하지 않은 승인 옵션입니다")
        pending.future.set_result(option_id)

    async def close(self) -> None:
        self.stopping = True
        await asyncio.gather(*(self.cancel(j) for j in list(self.tasks)), return_exceptions=True)
        await self.store.close()
