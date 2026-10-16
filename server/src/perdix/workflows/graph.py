"""완료 ledger를 우선하는 영속 StateGraph 작업 스케줄러."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Protocol, TypedDict
from uuid import uuid4

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import NodeCancelledError
from langgraph.graph import END, START, StateGraph

from perdix.harness.budget import BudgetExceeded
from perdix.llm.base import ProviderError
from perdix.workflows.models import TaskAttempt, TaskStatus, WorkflowRun, WorkflowTask
from perdix.workflows.projection import RunUpdated, WorkflowEvent, WorkflowSnapshot
from perdix.workflows.store import WorkflowStore
from perdix.workflows.worktrees import WorktreeError


class Executor(Protocol):
    async def execute(
        self,
        run: WorkflowRun,
        task: WorkflowTask,
        attempt_id: str,
        predecessors: tuple[TaskAttempt, ...],
    ) -> TaskAttempt: ...


type Finish = Callable[[WorkflowRun, tuple[TaskAttempt, ...]], Awaitable[tuple[str, ...]]]


class GraphState(TypedDict):
    remaining: list[str]
    revision: int
    done: bool


class WorkflowGraph:
    def __init__(
        self,
        store: WorkflowStore,
        executor: Executor,
        checkpoint_path: Path,
        finish: Finish | None = None,
    ) -> None:
        self.store = store
        self.executor = executor
        self.checkpoint_path = checkpoint_path
        self.finish = finish

    async def run(self, run: WorkflowRun, retry_tasks: tuple[str, ...] = ()) -> WorkflowSnapshot:
        """명시적 시작 이후 호출한다. 불명확한 기존 attempt는 자동 실행하지 않는다."""
        tasks = {task.task_id: task for task in run.plan.tasks}
        if set(retry_tasks) - tasks.keys():
            raise ValueError("정의되지 않은 재시도 작업입니다")
        snapshot = await self.store.snapshot(run.run_id)
        latest = {attempt.task_id: attempt for attempt in snapshot.attempts}
        invalidated = set(retry_tasks)
        while True:
            dependents = {t.task_id for t in tasks.values() if set(t.dependencies) & invalidated}
            expanded = invalidated | dependents
            if expanded == invalidated:
                break
            invalidated = expanded
        repair_seeds = {
            key: latest[key]
            for key in invalidated
            if key in latest and latest[key].status == "completed"
        }
        order = tuple(
            TopologicalSorter({k: t.dependencies for k, t in tasks.items()}).static_order()
        )
        completed = {
            key: value
            for key, value in latest.items()
            if value.status == "completed" and key not in invalidated
        }
        remaining = [key for key in tasks if key not in completed]
        counts = {key: sum(a.task_id == key for a in snapshot.attempts) for key in tasks}
        unsafe = any(
            key in latest and key not in invalidated and latest[key].status != "completed"
            for key in remaining
        )
        if unsafe:
            return await self._status(run, "blocked")

        async def dispatch(state: GraphState) -> GraphState:
            ready = [
                key
                for key in state["remaining"]
                if all(dep in completed for dep in tasks[key].dependencies)
            ]
            if not ready:
                for key in state["remaining"]:
                    await self.store.save_attempt(
                        TaskAttempt(
                            run_id=run.run_id,
                            task_id=key,
                            attempt_id=uuid4().hex,
                            owner_epoch=run.owner_epoch,
                            status="blocked",
                            failure_reason="선행 작업이 완료되지 않았습니다",
                        )
                    )
                return {**state, "done": True}
            selected = ready[: run.plan.max_parallel_tasks]

            async def execute(key: str) -> TaskAttempt:
                if counts[key] >= 1 + min(2, run.plan.max_revision_rounds):
                    return await self.store.save_attempt(
                        TaskAttempt(
                            run_id=run.run_id,
                            task_id=key,
                            attempt_id=uuid4().hex,
                            owner_epoch=run.owner_epoch,
                            status="blocked",
                            failure_reason="작업 수정 재시도 상한을 소진했습니다",
                        )
                    )
                counts[key] += 1
                attempt_id = uuid4().hex
                initial = TaskAttempt(
                    run_id=run.run_id,
                    task_id=key,
                    attempt_id=attempt_id,
                    owner_epoch=run.owner_epoch,
                    status="running",
                )
                await self.store.save_attempt(initial)
                ancestors: set[str] = set()

                def collect(task_id: str) -> None:
                    for dep in tasks[task_id].dependencies:
                        collect(dep)
                        ancestors.add(dep)

                collect(key)
                predecessors = tuple(completed[k] for k in order if k in ancestors)
                if key in repair_seeds:
                    predecessors += (repair_seeds[key],)
                try:
                    result = await self.executor.execute(
                        run, tasks[key], initial.attempt_id, predecessors
                    )
                except (ProviderError, WorktreeError, BudgetExceeded) as error:
                    latest_snapshot = await self.store.snapshot(run.run_id)
                    initial = next(
                        a for a in latest_snapshot.attempts if a.attempt_id == initial.attempt_id
                    )
                    result = initial.model_copy(
                        update={"status": "failed", "failure_reason": str(error)}
                    )
                except asyncio.CancelledError:
                    latest_snapshot = await self.store.snapshot(run.run_id)
                    initial = next(
                        a for a in latest_snapshot.attempts if a.attempt_id == initial.attempt_id
                    )
                    await self.store.save_attempt(
                        initial.model_copy(update={"status": "interrupted"})
                    )
                    raise
                return await self.store.save_attempt(result)

            pending_tasks = [asyncio.create_task(execute(key)) for key in selected]
            try:
                results = await asyncio.gather(*pending_tasks)
            finally:
                for pending_task in pending_tasks:
                    if not pending_task.done():
                        pending_task.cancel()
                await asyncio.gather(*pending_tasks, return_exceptions=True)
            for result in results:
                if result.status == "completed":
                    completed[result.task_id] = result
            pending = [key for key in state["remaining"] if key not in selected]
            return {**state, "remaining": pending, "done": not pending}

        async def finish(state: GraphState) -> GraphState:
            if len(completed) != len(tasks) or self.finish is None:
                return {**state, "done": True}
            repair = await self.finish(run, tuple(completed.values()))
            if set(repair) - tasks.keys():
                raise ValueError("리뷰가 정의되지 않은 작업을 지정했습니다")
            if not repair:
                return {**state, "done": True}
            if state["revision"] >= min(2, run.plan.max_revision_rounds):
                completed.clear()
                return {**state, "done": True}
            affected = set(repair)
            while True:
                expanded = affected | {
                    t.task_id for t in tasks.values() if set(t.dependencies) & affected
                }
                if expanded == affected:
                    break
                affected = expanded
            for key in affected:
                if key in completed:
                    repair_seeds[key] = completed.pop(key)
            return {
                "remaining": [k for k in tasks if k in affected],
                "revision": state["revision"] + 1,
                "done": False,
            }

        builder = StateGraph(GraphState)
        builder.add_node("dispatch", dispatch)
        builder.add_node("finish", finish)
        builder.add_edge(START, "dispatch")
        builder.add_conditional_edges(
            "dispatch", lambda state: "finish" if state["done"] else "dispatch"
        )
        builder.add_conditional_edges("finish", lambda state: END if state["done"] else "dispatch")
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        await self._status(run, "running")
        try:
            async with AsyncSqliteSaver.from_conn_string(str(self.checkpoint_path)) as saver:
                graph = builder.compile(checkpointer=saver)
                await graph.ainvoke(
                    GraphState(
                        remaining=remaining,
                        revision=max(0, max(counts.values(), default=0) - 1),
                        done=False,
                    ),
                    config={"configurable": {"thread_id": run.run_id}, "recursion_limit": 100},
                )
        except (asyncio.CancelledError, NodeCancelledError):
            return await self._status(run, "interrupted")
        except Exception:  # noqa: BLE001  # BROAD_EXCEPT_OK
            logging.getLogger(__name__).exception("워크플로 그래프 실행 실패")
            await self._status(run, "failed")
            raise
        return await self._status(run, "completed" if len(completed) == len(tasks) else "blocked")

    async def _status(self, run: WorkflowRun, status: TaskStatus) -> WorkflowSnapshot:
        snapshot = await self.store.snapshot(run.run_id)
        await self.store.append_event(
            WorkflowEvent(
                run_id=run.run_id,
                owner_epoch=run.owner_epoch,
                idempotency_key=str(uuid4()),
                payload=RunUpdated(
                    run=snapshot.run.model_copy(
                        update={
                            "status": status,
                            "failure_reason": "워크플로 그래프 실행 중 예상하지 못한 오류"
                            if status == "failed"
                            else None
                            if status in {"running", "completed"}
                            else snapshot.run.failure_reason,
                        }
                    )
                ),
            )
        )
        return await self.store.snapshot(run.run_id)
