"""서비스 단계의 SQLite 기록과 프로세스 연결 재생."""

from pathlib import Path

import pytest
from test_store import run_model

from perdix.workflows.projection import WorkflowEvent
from perdix.workflows.store import WorkflowStore


@pytest.mark.asyncio
@pytest.mark.parametrize("phase_kind", ["finish", "accept"])
async def test_workflow_phase_and_process_survive_reopen(tmp_path: Path, phase_kind: str) -> None:
    # Given: 작업 attempt와 독립적인 서비스 단계가 먼저 기록된다.
    from perdix.workflows.projection import ProcessObserved, WorkflowPhaseUpdated

    path = tmp_path / "workflows.sqlite3"
    store = WorkflowStore(path)
    await store.create_run(run_model(tmp_path))
    phase = WorkflowPhaseUpdated.model_validate(
        {
            "phase_id": "service-phase",
            "phase": phase_kind,
            "status": "running",
        }
    )
    await store.append_event(
        WorkflowEvent(run_id="run", idempotency_key="phase-start", payload=phase)
    )
    await store.append_event(
        WorkflowEvent(
            run_id="run",
            idempotency_key="process-start",
            payload=ProcessObserved(
                attempt_id=phase.phase_id, pid=123, process_group=123, phase="started"
            ),
        )
    )
    # When: 재연결한 복구자가 미완료 단계를 unknown으로 기록한다.
    reopened = WorkflowStore(path)
    before = await reopened.snapshot("run")
    await reopened.append_event(
        WorkflowEvent(
            run_id="run",
            idempotency_key="phase-unknown",
            payload=phase.model_copy(update={"status": "unknown"}),
        )
    )
    # Then: 프로세스 연결과 unknown 단계가 재생에도 보존된다.
    after = await reopened.snapshot("run")
    assert before.phases == (phase,)
    assert before.processes[0].attempt_id == phase.phase_id
    assert len(after.phases) == 1 and after.phases[0].status == "unknown"
    assert after == await reopened.replay("run")
