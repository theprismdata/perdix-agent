import asyncio
import json
import time
from pathlib import Path
from typing import Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic.alias_generators import to_camel

JobStatus = Literal[
    "queued", "running", "waiting", "completed", "failed", "cancelled", "interrupted"
]


class Job(BaseModel):
    model_config = ConfigDict(frozen=True, alias_generator=to_camel, populate_by_name=True)
    id: str
    project: str
    prompt: str
    source: str
    source_id: str | None
    created_at: float
    status: JobStatus = "queued"
    session_id: str | None = None
    result: str = ""
    workflow_run_id: str | None = None


class JobEvent(BaseModel):
    model_config = ConfigDict(frozen=True, alias_generator=to_camel, populate_by_name=True)
    seq: int
    job_id: str
    kind: str
    data: JsonValue
    created_at: float


class GatewayStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.db: aiosqlite.Connection | None = None
        self.changed = asyncio.Condition()

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.path.chmod(0o600)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.execute(
            "CREATE TABLE IF NOT EXISTS gateway_events ("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, "
            "kind TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL, "
            "source_id TEXT UNIQUE)"
        )
        await self.db.commit()

    async def close(self) -> None:
        if self.db:
            await self.db.close()

    async def append(
        self,
        job_id: str,
        kind: str,
        data: JsonValue,
        source_id: str | None = None,
    ) -> None:
        assert self.db is not None
        await self.db.execute(
            "INSERT INTO gateway_events(job_id,kind,data,created_at,source_id) VALUES(?,?,?,?,?)",
            (job_id, kind, json.dumps(data, ensure_ascii=False), time.time(), source_id),
        )
        await self.db.commit()
        async with self.changed:
            self.changed.notify_all()

    async def source_job(self, source_id: str) -> str | None:
        assert self.db is not None
        async with self.db.execute(
            "SELECT job_id FROM gateway_events WHERE source_id=?",
            (source_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return str(row[0]) if row else None

    async def events(self, job_id: str, after: int = 0) -> list[JobEvent]:
        assert self.db is not None
        async with self.db.execute(
            "SELECT seq,job_id,kind,data,created_at FROM gateway_events "
            "WHERE job_id=? AND seq>? ORDER BY seq LIMIT 1000",
            (job_id, after),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            JobEvent(seq=r[0], job_id=r[1], kind=r[2], data=json.loads(r[3]), created_at=r[4])
            for r in rows
        ]

    async def jobs(self) -> list[Job]:
        assert self.db is not None
        async with self.db.execute(
            "SELECT data FROM gateway_events WHERE kind='submitted' ORDER BY seq DESC LIMIT 200",
        ) as cursor:
            rows = await cursor.fetchall()
        jobs = []
        for row in rows:
            job = await self.get(Job.model_validate_json(row[0]).id)
            if job is not None:
                jobs.append(job)
        return jobs

    async def bind_workflow(self, job_id: str, session_id: str, run_id: str) -> None:
        job = await self.get(job_id)
        if job is None:
            raise ValueError("작업을 찾을 수 없습니다")
        if job.session_id == session_id and job.workflow_run_id == run_id:
            return
        await self.append(job_id, "workflow_binding", {
            "session_id": session_id, "workflow_run_id": run_id,
        })

    async def get(self, job_id: str) -> Job | None:
        assert self.db is not None
        async with self.db.execute(
            "SELECT data FROM gateway_events WHERE job_id=? AND kind='submitted'",
            (job_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        job = Job.model_validate_json(row[0])
        async with self.db.execute(
            "SELECT data FROM gateway_events WHERE job_id=? "
            "AND kind IN ('state','workflow_binding') ORDER BY seq",
            (job_id,),
        ) as cursor:
            states = await cursor.fetchall()
        data = job.model_dump()
        for state in states:
            data.update(json.loads(state[0]))
        return Job.model_validate(data)
