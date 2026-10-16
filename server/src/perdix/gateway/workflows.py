"""인증된 Gateway의 프로젝트별 읽기 전용 워크플로·팀 투영."""

import json
from pathlib import Path
from typing import Annotated, Literal

import aiosqlite
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from perdix.gateway.events import Job
from perdix.gateway.runtime import GatewayRuntime, WorkflowBusyError
from perdix.workflows.models import WorkflowModel
from perdix.workflows.projection import WorkflowEvent, WorkflowSnapshot


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["start", "resume", "retry", "accept", "reject"]
    task_id: str | None = Field(default=None, alias="taskId", min_length=1)


class CursorEvent(WorkflowEvent):
    seq: int


class EventPage(WorkflowModel):
    events: tuple[CursorEvent, ...]
    seq: int


class TeamSummary(BaseModel):
    id: str
    name: str
    project: str


class TeamMember(BaseModel):
    name: str
    role: str
    active: bool


class TeamTask(BaseModel):
    id: str
    owner: str
    body: str
    paths: list[str]
    state: str
    result: str


class TeamSnapshot(TeamSummary):
    members: list[TeamMember]
    tasks: list[TeamTask]
    seq: int


class TeamList(BaseModel):
    teams: list[TeamSummary]


def workflow_router(runtime: GatewayRuntime) -> APIRouter:
    router = APIRouter()
    config = runtime.config
    workflow_path = Path(config.server.db_path).resolve().with_name("workflows.sqlite3")

    def project_root(project: str) -> str:
        configured = config.projects.get(project)
        if configured is None:
            raise HTTPException(404, "등록되지 않은 프로젝트입니다")
        return str(configured.cwd.resolve())

    async def snapshot(job_id: str) -> WorkflowSnapshot:
        job = await runtime.store.get(job_id)
        if job is None or job.workflow_run_id is None or not workflow_path.is_file():
            raise HTTPException(404, "연결된 워크플로가 없습니다")
        root = project_root(job.project)
        async with aiosqlite.connect(workflow_path.as_uri() + "?mode=ro", uri=True) as db:
            async with db.execute(
                "SELECT snapshot FROM workflow_runs WHERE run_id=? AND project_root=?",
                (job.workflow_run_id, root),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            raise HTTPException(404, "연결된 워크플로가 없습니다")
        result = WorkflowSnapshot.model_validate_json(row[0])
        if result.run.session_id != job.session_id or result.run.project_root != root:
            raise HTTPException(404, "연결된 워크플로가 없습니다")
        return result

    @router.get("/api/jobs/{job_id}/workflow")
    async def workflow(job_id: str) -> WorkflowSnapshot:
        return await snapshot(job_id)

    @router.get("/api/jobs/{job_id}/workflow/events")
    async def events(job_id: str, after: Annotated[int, Query(ge=0)] = 0) -> EventPage:
        current = await snapshot(job_id)
        # 조회 시작 시점의 seq까지 모두 반환해 1000개를 넘는 재접속 gap도 건너뛰지 않는다.
        async with aiosqlite.connect(workflow_path.as_uri() + "?mode=ro", uri=True) as db:
            async with db.execute(
                "SELECT event FROM workflow_events WHERE run_id=? AND seq>? AND seq<=? "
                "ORDER BY seq", (current.run.run_id, after, current.seq),
            ) as cursor:
                rows = await cursor.fetchall()
        items = [WorkflowEvent.model_validate_json(row[0]) for row in rows]
        return EventPage(events=tuple(
            CursorEvent(**item.model_dump(), seq=item.event_seq) for item in items
        ), seq=current.seq)

    @router.post("/api/jobs/{job_id}/workflow/control", status_code=202)
    async def control(job_id: str, request: ControlRequest) -> Job:
        await snapshot(job_id)
        try:
            return await runtime.control(job_id, request.action, request.task_id)
        except WorkflowBusyError as error:
            raise HTTPException(409, str(error)) from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    @router.get("/api/teams")
    async def teams(project: str) -> TeamList:
        root = project_root(project)
        path = config.server.team_db.resolve()
        if not path.is_file():
            return TeamList(teams=[])
        async with aiosqlite.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            async with db.execute(
                "SELECT id,name,project FROM teams WHERE project=? ORDER BY name,id", (root,),
            ) as cursor:
                rows = await cursor.fetchall()
        return TeamList(teams=[TeamSummary(id=r[0], name=r[1], project=r[2]) for r in rows])

    @router.get("/api/teams/{team_id}")
    async def team(team_id: str, project: str) -> TeamSnapshot:
        root = project_root(project)
        path = config.server.team_db.resolve()
        if not path.is_file():
            raise HTTPException(404, "팀을 찾을 수 없습니다")
        async with aiosqlite.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            async with db.execute(
                "SELECT id,name,project FROM teams WHERE id=? AND project=?", (team_id, root),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise HTTPException(404, "팀을 찾을 수 없습니다")
            async with db.execute(
                "SELECT name,role,active FROM members WHERE team=? ORDER BY name", (team_id,),
            ) as cursor:
                members = [TeamMember(**dict(r)) for r in await cursor.fetchall()]
            async with db.execute(
                "SELECT id,owner,body,paths,state,result FROM tasks WHERE team=? ORDER BY id",
                (team_id,),
            ) as cursor:
                tasks = [TeamTask(**(dict(r) | {"paths": json.loads(r["paths"])}))
                         for r in await cursor.fetchall()]
            async with db.execute(
                "SELECT COALESCE(MAX(id),0) FROM team_events WHERE team=?", (team_id,),
            ) as cursor:
                sequence = await cursor.fetchone()
        assert sequence is not None
        return TeamSnapshot(**dict(row), members=members, tasks=tasks, seq=sequence[0])

    return router
