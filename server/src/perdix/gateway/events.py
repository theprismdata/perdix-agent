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
    agent: str = "perdix"
    model: str | None = None


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

    async def selected_model(self, conversation: str) -> str | None:
        assert self.db is not None
        async with self.db.execute(
            "SELECT data FROM gateway_events WHERE job_id=? AND kind='telegramModel' "
            "ORDER BY seq DESC LIMIT 1", (conversation,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        value = json.loads(row[0])
        return value if isinstance(value, str) else None

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
            job = Job.model_validate_json(row[0])
            # 작업 상태는 마지막 상태 이벤트만 투영한다. 로그 자체는 변경하지 않는다.
            async with self.db.execute(
                "SELECT data FROM gateway_events WHERE job_id=? AND kind='state' "
                "ORDER BY seq DESC LIMIT 1",
                (job.id,),
            ) as cursor:
                state = await cursor.fetchone()
            if state:
                job = Job.model_validate({**job.model_dump(), **json.loads(state[0])})
            jobs.append(job)
        return jobs

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
            "SELECT data FROM gateway_events WHERE job_id=? AND kind='state' "
            "ORDER BY seq DESC LIMIT 1",
            (job_id,),
        ) as cursor:
            state = await cursor.fetchone()
        return Job.model_validate({**job.model_dump(), **json.loads(state[0])}) if state else job
