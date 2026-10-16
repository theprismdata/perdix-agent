"""사용자 ACP 명령과 영속 워크플로 서비스의 연결."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from acp.schema import AgentMessageChunk, AgentPlanUpdate, PlanEntry, TextContentBlock

from perdix.harness.turn import TurnResult
from perdix.workflows.commands import WorkflowCommand
from perdix.workflows.projection import WorkflowSnapshot
from perdix.workflows.service import WorkflowContext, WorkflowService


def snapshot_update(snapshot: WorkflowSnapshot) -> AgentPlanUpdate:
    """표준 plan과 선택적 metadata를 같은 update에 둔다."""
    latest = {attempt.task_id: attempt for attempt in snapshot.attempts}
    entries: list[PlanEntry] = []
    for task in snapshot.plan.tasks:
        attempt = latest.get(task.task_id)
        status = attempt.status if attempt else "pending"
        entries.append(PlanEntry(
            content=f"[{task.task_id}] {task.title} ({status})", priority="medium",
            status="completed" if status == "completed" else (
                "in_progress" if status == "running" else "pending"
            ),
        ))
    return AgentPlanUpdate(session_update="plan", entries=entries, field_meta={
        "perdix": {"workflow": {"schemaVersion": 1, "kind": "snapshot",
            "runId": snapshot.run.run_id, "seq": snapshot.seq,
            "payload": snapshot.model_dump(mode="json", by_alias=True)}}
    })


def trusted_config(root: Path, trusted: bool) -> str:
    if not trusted:
        return "{}"
    path = root / ".perdix" / "config.json"
    return path.read_text(encoding="utf-8") if path.exists() else "{}"


async def execute_workflow(
    service: WorkflowService, command: WorkflowCommand, context: WorkflowContext,
    emit: Callable[[AgentPlanUpdate | AgentMessageChunk], Awaitable[None]],
) -> TurnResult:
    """실행 중에도 투영을 보내며 취소 이벤트는 기존 세션 슬롯을 공유한다."""
    seen: dict[str, int] = {}

    async def publish(snapshot: WorkflowSnapshot) -> None:
        if seen.get(snapshot.run.run_id) != snapshot.seq:
            await emit(snapshot_update(snapshot))
            seen[snapshot.run.run_id] = snapshot.seq

    async def poll() -> None:
        while True:
            if command.run_id is not None:
                run = await service.store.get_run(command.run_id, str(context.project_root))
                if run is not None and run.session_id == context.session_id:
                    await publish(await service.store.snapshot(run.run_id))
            await asyncio.sleep(0.1)

    polling = asyncio.create_task(poll())
    try:
        snapshot = await service.execute(command, context)
    finally:
        polling.cancel()
        await asyncio.gather(polling, return_exceptions=True)
    await publish(snapshot)
    await emit(AgentMessageChunk(session_update="agent_message_chunk", content=TextContentBlock(
        type="text", text=f"Workflow {snapshot.run.run_id}: {snapshot.run.status}; "
        f"verification={snapshot.run.verification_status}; "
        f"acceptance={snapshot.run.acceptance_status}\n",
    )))
    return TurnResult(
        stop_reason="cancelled" if context.cancel.is_set() else "end_turn",
        verification="failed" if snapshot.run.status in {"failed", "blocked"}
        or snapshot.run.verification_status == "failed" else None,
    )
