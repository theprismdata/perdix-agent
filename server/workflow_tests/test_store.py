"""실제 SQLite 파일의 재개·원자성·소유권 계약."""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from perdix.workflows.models import TaskAttempt, WorkflowPlan, WorkflowRun, WorkflowTask
from perdix.workflows.projection import RunUpdated, WorkflowEvent, WorkflowStateError
from perdix.workflows.store import (
    WorkflowBudgetExceeded,
    WorkflowOwnerError,
    WorkflowSchemaError,
    WorkflowStore,
)


def run_model(root: Path, run_id: str = "run") -> WorkflowRun:
    return WorkflowRun(
        run_id=run_id,
        session_id="session",
        project_root=str(root),
        base_commit="base",
        target_branch="main",
        plan=WorkflowPlan(
            objective="test",
            shared_steps_limit=2000,
            tasks=(
                WorkflowTask(
                    task_id="task",
                    title="task",
                    role="implement",
                    model="fake",
                    paths=("src",),
                    acceptance=("works",),
                ),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_completed_attempt_survives_reopen_and_replays(tmp_path: Path) -> None:
    # Given: 실제 파일과 완료 전 ledger.
    path = tmp_path / "workflows.sqlite3"
    store = WorkflowStore(path)
    await store.create_run(run_model(tmp_path))
    attempt = TaskAttempt(
        run_id="run",
        task_id="task",
        attempt_id="a1",
        owner_epoch=0,
        status="completed",
        candidate_commit="candidate",
    )
    completed = await store.save_attempt(attempt)
    await store.close()
    # When: graph 체크포인트 없이 다른 저장소 인스턴스가 연다.
    reopened = WorkflowStore(path)
    replayed = await reopened.replay("run")
    # Then: 완료 결과는 재사용 가능하고 이벤트 재생은 저장 투영과 같다.
    assert await reopened.completed_attempt("run", "a1") == completed
    assert replayed == await reopened.snapshot("run")
    assert replayed.run.verification_status == "unverified"


@pytest.mark.asyncio
async def test_duplicate_append_is_idempotent_and_conflict_rolls_back(tmp_path: Path) -> None:
    # Given: 하나의 고정 멱등 키.
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    run = (await store.create_run(run_model(tmp_path))).run
    event = WorkflowEvent(
        run_id="run",
        idempotency_key="status",
        payload=RunUpdated(run=run.model_copy(update={"status": "running"})),
    )
    # When: 같은 이벤트가 동시에 중복 전달되고 다른 내용도 같은 키를 사용한다.
    results = await asyncio.gather(*(store.append_event(event) for _ in range(8)))
    with pytest.raises(WorkflowStateError):
        await store.append_event(event.model_copy(update={"payload": RunUpdated(run=run)}))
    # Then: 이벤트 1개만 append되고 충돌은 투영을 바꾸지 않는다.
    assert len({result.event_seq for result in results}) == 1
    assert len(await store.events("run")) == 2
    assert (await store.snapshot("run")).run.status == "running"


@pytest.mark.asyncio
async def test_unknown_schema_is_rejected_without_mutation(tmp_path: Path) -> None:
    # Given: 미래 버전의 SQLite 파일.
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
        db.execute("CREATE TABLE future (value TEXT)")
    before = path.read_bytes()
    # When: 현재 서버가 연다.
    with pytest.raises(WorkflowSchemaError):
        await WorkflowStore(path).open()
    # Then: 미래 DB의 바이트까지 보존한다.
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_previous_owner_result_is_rejected_across_connections(tmp_path: Path) -> None:
    # Given: 서로 다른 연결에서 증가하는 owner_epoch.
    path = tmp_path / "workflows.sqlite3"
    first, second = WorkflowStore(path), WorkflowStore(path)
    await first.create_run(run_model(tmp_path))
    epochs = await asyncio.gather(
        first.claim_owner("run", "first"), second.claim_owner("run", "second")
    )
    # When: 이전 소유자가 늦은 완료 결과를 기록한다.
    with pytest.raises(WorkflowOwnerError):
        await first.save_attempt(
            TaskAttempt(
                run_id="run",
                task_id="task",
                attempt_id="late",
                owner_epoch=min(epochs),
                status="completed",
            )
        )
    # Then: 늦은 완료가 ledger에 없고 새 epoch만 유효하다.
    assert sorted(epochs) == [1, 2]
    assert await second.completed_attempt("run", "late") is None
    await second.assert_owner("run", 2)


@pytest.mark.asyncio
async def test_pagination_exceeds_thousand_and_replay_is_complete(tmp_path: Path) -> None:
    # Given: 한 페이지보다 많은 실제 이벤트.
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    await store.create_run(run_model(tmp_path))
    for _ in range(1004):
        await store.reserve_step("run", 0, 2000)
    # When: 마지막 sequence로 두 번째 페이지를 읽고 재생한다.
    first = await store.events("run")
    second = await store.events("run", first[-1].event_seq)
    replayed = await store.replay("run")
    # Then: 누락·중복 없이 1005개이며 누적 예산도 보존한다.
    assert len(first) == 1000 and len(second) == 5
    assert [e.event_seq for e in first + second] == list(range(1, 1006))
    assert replayed.steps_used == 1004
    assert replayed == await store.snapshot("run")


@pytest.mark.asyncio
async def test_transaction_rollback_keeps_event_and_snapshot_atomic(tmp_path: Path) -> None:
    # Given: snapshot 갱신 때 디스크 오류를 흉내 내는 실제 SQLite 트리거.
    path = tmp_path / "workflows.sqlite3"
    store = WorkflowStore(path)
    run = (await store.create_run(run_model(tmp_path))).run
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TRIGGER fail_projection BEFORE UPDATE ON workflow_runs "
            "BEGIN SELECT RAISE(ABORT,'simulated storage failure'); END"
        )
    # When: 이벤트 insert 뒤 projection 갱신이 실패한다.
    with pytest.raises(sqlite3.IntegrityError, match="simulated storage failure"):
        await store.save_run(run.model_copy(update={"status": "running"}))
    # Then: 재연결해도 미완성 이벤트가 없고 기존 상태 그대로다.
    reopened = WorkflowStore(path)
    assert len(await reopened.events("run")) == 1
    assert (await reopened.snapshot("run")).run.status == "pending"


@pytest.mark.asyncio
async def test_budget_reservation_is_atomic_and_time_is_cumulative(tmp_path: Path) -> None:
    # Given: 하나 남은 공유 Step와 첫 소유자의 누적 시간.
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    await store.create_run(run_model(tmp_path))
    epoch = await store.claim_owner("run", "first")
    await store.checkpoint_active_time("run", epoch, 2.0)
    await store.finish_owner("run", epoch, 3.0)
    next_epoch = await store.claim_owner("run", "second")
    # When: 동시에 마지막 Step을 예약하고 새 소유자가 시간을 더한다.
    results = await asyncio.gather(
        *(store.reserve_step("run", next_epoch, 1) for _ in range(5)), return_exceptions=True
    )
    await store.checkpoint_active_time("run", next_epoch, 4.0)
    await store.checkpoint_active_time("run", next_epoch, 4.0)
    # Then: 예약 하나만 성공하고 중복 시간 보고로 예산이 늘지 않는다.
    assert results.count(1) == 1
    assert sum(isinstance(result, WorkflowBudgetExceeded) for result in results) == 4
    snapshot = await store.snapshot("run")
    assert snapshot.elapsed_seconds == 7.0
    assert snapshot.steps_used == 1
    assert snapshot == await store.replay("run")


@pytest.mark.asyncio
async def test_project_scoping_and_immutable_history(tmp_path: Path) -> None:
    # Given: 두 프로젝트와 변경 불가 이벤트.
    path = tmp_path / "workflows.sqlite3"
    store = WorkflowStore(path)
    await store.create_run(run_model(tmp_path, "first"))
    await store.create_run(run_model(tmp_path / "other", "second"))
    # When: 프로젝트로 조회하고 과거 이벤트를 수정하려 한다.
    runs = await store.list_runs(str(tmp_path))
    with sqlite3.connect(path) as db, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("DELETE FROM workflow_events")
    # Then: 다른 프로젝트는 조회 결과에 포함되지 않는다.
    assert [run.run_id for run in runs] == ["first"]
    assert await store.get_run("second", str(tmp_path)) is None
    assert (await store.snapshot("first")).seq == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [False, True])
async def test_process_death_preserves_only_committed_completion(
    tmp_path: Path,
    committed: bool,
) -> None:
    # Given: 자식 프로세스가 실제 저장 트랜잭션 도중 강제 종료한다.
    path = tmp_path / "workflows.sqlite3"
    store = WorkflowStore(path)
    await store.create_run(run_model(tmp_path))
    script = """
import asyncio, os, sys
from pathlib import Path
from perdix.workflows.models import TaskAttempt
from perdix.workflows.projection import AttemptUpdated, WorkflowEvent
from perdix.workflows.store import WorkflowStore
from perdix.workflows.store_sqlite import transaction, persist
async def main():
    path = Path(sys.argv[1])
    attempt = TaskAttempt(run_id='run', task_id='task', attempt_id='crash',
                          owner_epoch=0, status='completed', candidate_commit='durable')
    if sys.argv[2] == 'True':
        await WorkflowStore(path).save_attempt(attempt)
        os._exit(17)
    async with transaction(path) as db:
        await persist(db, WorkflowEvent(run_id='run', idempotency_key='crash',
                                       payload=AttemptUpdated(attempt=attempt)))
        os._exit(17)
asyncio.run(main())
"""
    # When: commit 직전 또는 직후 실제 프로세스가 종료한다.
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script, str(path), str(committed)],
        capture_output=True,
        timeout=10,
    )
    # Then: 미커밋 결과는 사라지고 커밋 결과는 체크포인트 없이도 복구된다.
    assert result.returncode == 17, result.stderr.decode()
    reopened = WorkflowStore(path)
    completed = await reopened.completed_attempt("run", "crash")
    assert (completed is not None) is committed
    assert (await reopened.snapshot("run")).seq == (2 if committed else 1)
    assert await reopened.replay("run") == await reopened.snapshot("run")


@pytest.mark.asyncio
async def test_stale_time_sample_cannot_decrease_or_reactivate_owner(tmp_path: Path) -> None:
    # Given: 두 coroutine의 시간이 실제 저장 순서와 반대다.
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    await store.create_run(run_model(tmp_path))
    epoch = await store.claim_owner("run", "owner")
    await store.checkpoint_active_time("run", epoch, 5)
    # When: 늦게 들어온 이전 샘플과 종료 뒤의 샘플을 처리한다.
    stale = await store.checkpoint_active_time("run", epoch, 3)
    finished = await store.finish_owner("run", epoch, 4)
    late = await store.checkpoint_active_time("run", epoch, 6)
    # Then: 누적 시간과 종료 상태가 보존된다.
    assert stale == finished == late == 5
    assert not (await store.snapshot("run")).owner_active


@pytest.mark.asyncio
async def test_blocked_failure_reason_survives_reopen_and_replay(tmp_path: Path) -> None:
    # Given: 이전 형식에는 실패 이유 필드가 없다.
    path = tmp_path / "workflows.sqlite3"
    store = WorkflowStore(path)
    legacy = run_model(tmp_path).model_dump(exclude={"failure_reason"})
    created = await store.create_run(WorkflowRun.model_validate(legacy))
    reason = "리뷰 결과 형식이 올바르지 않습니다"
    # When: 리뷰 실패로 blocked 상태와 실패 이유를 함께 기록한다.
    await store.save_run(
        created.run.model_copy(
            update={
                "status": "blocked",
                "failure_reason": reason,
            }
        )
    )
    reopened = WorkflowStore(path)
    # Then: 기본값은 None이고 재연결·재생 모두 같은 실패 이유를 보존한다.
    snapshot = await reopened.snapshot("run")
    assert created.run.failure_reason is None
    assert snapshot.run.status == "blocked"
    assert snapshot.run.failure_reason == reason
    assert snapshot == await reopened.replay("run")
