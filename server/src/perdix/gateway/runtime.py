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
from perdix.sandbox.base import scrub_env

logger = logging.getLogger(__name__)


class WorkflowBusyError(ValueError):
    """이미 실행 중인 세션에 제어 명령을 중첩할 수 없다."""



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
        metadata = update.field_meta or {}
        workflow = metadata.get("perdix", {}).get("workflow", {})
        run_id = workflow.get("runId")
        if isinstance(run_id, str) and run_id:
            await self.gateway.store.bind_workflow(self.job_id, session_id, run_id)
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
        self.task_projects: dict[str, str] = {}
        self.connections: dict[str, tuple[Agent, str]] = {}
        self.permissions: dict[str, PendingPermission] = {}
        self.processes: dict[str, AsyncExitStack] = {}
        self.clients: dict[str, JobClient] = {}
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
            job = Job(
                id=str(uuid4()),
                project=request.project,
                prompt=request.prompt,
                source=source,
                source_id=source_id,
                created_at=time.time(),
            )
            await self.store.append(job.id, "submitted", job.model_dump(mode="json"), source_id)
            self.task_projects[job.id] = job.project
            self.tasks[job.id] = asyncio.create_task(self._run(job), name=f"gateway-job-{job.id}")
            return job

    async def control(self, job_id: str, action: str, task_id: str | None = None) -> Job:
        async with self.submit_lock:
            job = await self.store.get(job_id)
            if job is None or job.workflow_run_id is None or job.session_id is None:
                raise ValueError("워크플로에 연결된 작업이 아닙니다")
            if action not in {"start", "resume", "retry", "accept", "reject"}:
                raise ValueError("지원하지 않는 제어 명령입니다")
            if action == "retry" and not task_id:
                raise ValueError("retry에는 taskId가 필요합니다")
            if task_id and any(character.isspace() for character in task_id):
                raise ValueError("유효하지 않은 taskId입니다")
            if (self.stopping or job.id in self.tasks
                    or job.project in self.task_projects.values()
                    or self.project_locks[job.project].locked()):
                raise WorkflowBusyError("프로젝트 또는 워크플로가 실행 중입니다")
            prompt = f"/workflow {action} {job.workflow_run_id}"
            if task_id:
                prompt += f" {task_id}"
            self.task_projects[job.id] = job.project
            self.tasks[job.id] = asyncio.create_task(self._run(job, prompt))
            return job

    async def _run(self, job: Job, control_prompt: str | None = None) -> None:
        client = self.clients.setdefault(job.id, JobClient(self, job.id))
        client.text.clear()
        reconnect = False
        try:
            async with self.project_locks[job.project], self.limit:
                project = self.config.projects[job.project]
                server = self.config.server
                args = [
                    "-m",
                    "perdix",
                    "--provider",
                    server.provider,
                    "--model",
                    server.model,
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
                env = scrub_env(os.environ)
                for key in (
                    self.config.token_env,
                    self.config.telegram.token_env if self.config.telegram else "",
                    self.config.github.token_env if self.config.github else "",
                    self.config.github.webhook_secret_env if self.config.github else "",
                ):
                    env.pop(key, None)
                conn: Agent
                connection = self.connections.get(job.id)
                if connection is None:
                    process = AsyncExitStack()
                    self.processes[job.id] = process
                    conn, _ = await process.enter_async_context(spawn_agent_process(
                        client, sys.executable, *args, env=env, cwd=project.cwd,
                        transport_kwargs={"stderr": None}, use_unstable_protocol=True,
                    ))
                    async with asyncio.timeout(30):
                        await conn.initialize(protocol_version=PROTOCOL_VERSION,
                                              client_capabilities=ClientCapabilities())
                        if job.session_id:
                            await conn.load_session(session_id=job.session_id,
                                                    cwd=str(project.cwd), mcp_servers=[])
                            session_id = job.session_id
                        else:
                            session = await conn.new_session(cwd=str(project.cwd), mcp_servers=[])
                            session_id = session.session_id
                            await conn.set_session_mode(session_id=session_id, mode_id="build")
                    self.connections[job.id] = (conn, session_id)
                else:
                    conn, session_id = connection
                await self.store.append(
                    job.id,
                    "state",
                    {
                        "status": "running",
                        "session_id": session_id,
                    },
                )
                response = await conn.prompt(
                    session_id=session_id, prompt=[text_block(control_prompt or job.prompt)]
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
            reconnect = True
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
            bound = await self.store.get(job.id)
            if reconnect or bound is None or bound.workflow_run_id is None:
                self.connections.pop(job.id, None)
                closing_process = self.processes.pop(job.id, None)
                if closing_process is not None:
                    await closing_process.aclose()
                self.clients.pop(job.id, None)
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
                self.task_projects.pop(job.id, None)

    async def cancel(self, job_id: str) -> None:
        task = self.tasks.get(job_id)
        if task:
            connection = self.connections.get(job_id)
            if connection:
                conn, session_id = connection
                await conn.cancel(session_id=session_id)
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=15)
                except TimeoutError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    process = self.processes.pop(job_id, None)
                    if process is not None:
                        await process.aclose()
                    self.connections.pop(job_id, None)
            else:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.tasks.pop(job_id, None)
            self.task_projects.pop(job_id, None)
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
        for process in self.processes.values():
            await process.aclose()
        self.processes.clear()
        self.connections.clear()
        await self.store.close()
