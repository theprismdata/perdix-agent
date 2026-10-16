"""워크플로 SQLite 스키마와 원자적 이벤트 저장 경계."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

import aiosqlite

from perdix.workflows.projection import (
    AttemptUpdated,
    WorkflowEvent,
    WorkflowSnapshot,
    WorkflowStateError,
    project,
)

SCHEMA_VERSION: Final = 1
SCHEMA: Final = (
    "CREATE TABLE IF NOT EXISTS workflow_runs (run_id TEXT PRIMARY KEY, "
    "project_root TEXT NOT NULL, snapshot TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS workflow_events (run_id TEXT NOT NULL, seq INTEGER NOT NULL, "
    "idempotency_key TEXT NOT NULL, event TEXT NOT NULL, PRIMARY KEY(run_id,seq), "
    "UNIQUE(run_id,idempotency_key))",
    "CREATE TABLE IF NOT EXISTS workflow_completions (run_id TEXT NOT NULL, "
    "attempt_id TEXT NOT NULL, attempt TEXT NOT NULL, PRIMARY KEY(run_id,attempt_id))",
    "CREATE TRIGGER IF NOT EXISTS workflow_events_immutable_update "
    "BEFORE UPDATE ON workflow_events BEGIN SELECT RAISE(ABORT,'immutable event'); END",
    "CREATE TRIGGER IF NOT EXISTS workflow_events_immutable_delete "
    "BEFORE DELETE ON workflow_events BEGIN SELECT RAISE(ABORT,'immutable event'); END",
)


class WorkflowSchemaError(RuntimeError):
    """현재 서버에서 해석할 수 없는 저장 형식."""


class WorkflowOwnerError(RuntimeError):
    """이전 소유자가 현재 실행에 결과를 쓰려고 했다."""


class WorkflowNotFoundError(LookupError):
    """요청한 워크플로 실행이 존재하지 않는다."""


@asynccontextmanager
async def transaction(path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """연결별 트랜잭션으로 프로세스와 coroutine 사이의 쓰기를 직렬화한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path, timeout=30) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute("PRAGMA user_version") as cursor:
                row = await cursor.fetchone()
            version = int(row[0]) if row else 0
            if version not in (0, SCHEMA_VERSION):
                raise WorkflowSchemaError(f"지원하지 않는 workflow schema: {version}")
            if version == 0:
                async with db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ) as cursor:
                    tables = await cursor.fetchall()
                if tables:
                    raise WorkflowSchemaError(
                        "버전 없는 기존 DB를 workflow DB로 사용할 수 없습니다"
                    )
                for statement in SCHEMA:
                    await db.execute(statement)
                await db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            yield db
            await db.commit()
        finally:
            if db.in_transaction:
                await db.rollback()


async def load_snapshot(db: aiosqlite.Connection, run_id: str) -> WorkflowSnapshot | None:
    async with db.execute("SELECT snapshot FROM workflow_runs WHERE run_id=?", (run_id,)) as c:
        row = await c.fetchone()
    return WorkflowSnapshot.model_validate_json(row[0]) if row else None


def check_owner(snapshot: WorkflowSnapshot, epoch: int) -> None:
    if snapshot.run.owner_epoch != epoch:
        raise WorkflowOwnerError(f"이전 owner_epoch: {epoch}; 현재: {snapshot.run.owner_epoch}")


async def persist(db: aiosqlite.Connection, event: WorkflowEvent) -> WorkflowEvent:
    """이벤트·투영·완료 ledger를 호출자의 동일 트랜잭션에 저장한다."""
    snapshot = await load_snapshot(db, event.run_id)
    async with db.execute(
        "SELECT event FROM workflow_events WHERE run_id=? AND idempotency_key=?",
        (event.run_id, event.idempotency_key),
    ) as c:
        duplicate = await c.fetchone()
    if snapshot is not None:
        expected = event.owner_epoch - (event.payload.kind == "owner_acquired")
        if duplicate is None:
            check_owner(snapshot, expected)
        elif event.owner_epoch != snapshot.run.owner_epoch:
            raise WorkflowOwnerError("이전 소유자의 중복 요청입니다")
    if duplicate:
        saved = WorkflowEvent.model_validate_json(duplicate[0])
        if saved.model_copy(update={"event_seq": 0}) != event.model_copy(update={"event_seq": 0}):
            raise WorkflowStateError("같은 idempotency key의 내용이 다릅니다")
        return saved
    saved = event.model_copy(update={"event_seq": snapshot.seq + 1 if snapshot else 1})
    updated = project(snapshot, saved)
    await db.execute(
        "INSERT INTO workflow_events VALUES(?,?,?,?)",
        (
            saved.run_id,
            saved.event_seq,
            saved.idempotency_key,
            saved.model_dump_json(),
        ),
    )
    await db.execute(
        "INSERT INTO workflow_runs VALUES(?,?,?) ON CONFLICT(run_id) DO UPDATE "
        "SET snapshot=excluded.snapshot",
        (saved.run_id, updated.run.project_root, updated.model_dump_json()),
    )
    match saved.payload:
        case AttemptUpdated(attempt=attempt) if attempt.status == "completed":
            completed = next(a for a in updated.attempts if a.attempt_id == attempt.attempt_id)
            await db.execute(
                "INSERT INTO workflow_completions VALUES(?,?,?)",
                (
                    saved.run_id,
                    completed.attempt_id,
                    completed.model_dump_json(),
                ),
            )
    return saved
