from __future__ import annotations

import asyncio
import json
import os
import select
import signal
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import TextIO

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from test_integration import allow, setup
from test_service import plan as service_plan
from test_service import setup as service_setup

from perdix.harness.budget import Budget
from perdix.harness.hooks import HookBus
from perdix.harness.modes import BUILD
from perdix.harness.turn import TurnRunner
from perdix.llm.fake import FakeProvider
from perdix.permissions.policy import PermissionPolicy
from perdix.sandbox.base import ExecResult, ProcessObservation
from perdix.sandbox.local import LocalBackend
from perdix.session.store import EventStore
from perdix.tools.fs import WriteTool
from perdix.tools.registry import ToolRegistry
from perdix.workflows import graph as graph_module
from perdix.workflows.commands import WorkflowCommand
from perdix.workflows.graph import WorkflowGraph
from perdix.workflows.integration import IntegrationService
from perdix.workflows.models import TaskAttempt, WorkflowPlan, WorkflowRun, WorkflowTask
from perdix.workflows.ownership import RunOwnership
from perdix.workflows.projection import AcceptanceRecorded, VerificationRecorded, WorkflowEvent
from perdix.workflows.recovery import process_observer, reconcile_acceptance, reconcile_run
from perdix.workflows.service import WorkflowContext, WorkflowService
from perdix.workflows.store import WorkflowStore
from perdix.workflows.verification import VerificationService
from perdix.workflows.worktrees import CandidateCommit, WorktreeService


def read_barrier(pipe: TextIO) -> str:
    assert select.select([pipe], [], [], 45)[0], "subprocess barrier timeout"
    return pipe.readline().strip()


def read_fifo(path: Path) -> str:
    descriptor = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    try:
        assert select.select([descriptor], [], [], 45)[0], "FIFO barrier timeout"
        return os.read(descriptor, 1024).decode()
    finally:
        os.close(descriptor)


def run_model(root: Path) -> WorkflowRun:
    return WorkflowRun(
        run_id="crash",
        session_id="session",
        project_root=str(root),
        base_commit="base",
        target_branch="main",
        plan=WorkflowPlan(
            objective="crash recovery",
            tasks=tuple(
                WorkflowTask(
                    task_id=task,
                    title=task,
                    role="implement",
                    model="fake",
                    paths=(task,),
                    acceptance=("marker once",),
                )
                for task in ("sibling", "target")
            ),
        ),
    )


async def worker(root: Path, barrier: str) -> None:
    if barrier.startswith("service_"):
        await service_worker(root, barrier)
        return
    store = WorkflowStore(root / "workflows.sqlite3")
    if barrier.startswith("accept_"):
        await accept_worker(root, barrier, store)
        return
    if barrier == "recover":
        async with RunOwnership(store, "crash", root / "locks"):
            result = await reconcile_run(store, "crash")
            snapshot = await store.snapshot("crash")
            if result.resumable and (root / "checkpoints.sqlite3").exists():
                await WorkflowGraph(store, MarkerExecutor(root), root / "checkpoints.sqlite3").run(
                    snapshot.run,
                )
            print(
                json.dumps({"result": asdict(result), "snapshot": snapshot.model_dump()}),
                flush=True,
            )
        return
    await store.create_run(run_model(root))
    async with RunOwnership(store, "crash", root / "locks") as owner:
        await store.save_attempt(
            TaskAttempt(
                run_id="crash",
                task_id="sibling",
                attempt_id="sibling-1",
                owner_epoch=owner.epoch,
                status="completed",
                candidate_commit="sibling-commit",
            )
        )
        if barrier.startswith("checkpoint_"):

            class BarrierSaver(AsyncSqliteSaver):
                async def aput(
                    self,
                    config: RunnableConfig,
                    checkpoint: Checkpoint,
                    metadata: CheckpointMetadata,
                    new_versions: ChannelVersions,
                ) -> RunnableConfig:
                    reached = checkpoint["channel_values"].get("remaining") == []
                    if reached and barrier == "checkpoint_before":
                        print("barrier", flush=True)
                        await asyncio.Event().wait()
                    result = await super().aput(config, checkpoint, metadata, new_versions)
                    if reached and barrier == "checkpoint_after":
                        print("barrier", flush=True)
                        await asyncio.Event().wait()
                    return result

            graph_module.AsyncSqliteSaver = BarrierSaver
            await WorkflowGraph(store, MarkerExecutor(root), root / "checkpoints.sqlite3").run(
                (await store.snapshot("crash")).run,
            )
            return
        if barrier == "dispatch_before":
            print("barrier", flush=True)
            await asyncio.Event().wait()
        attempt = TaskAttempt(
            run_id="crash",
            task_id="target",
            attempt_id="target-1",
            owner_epoch=owner.epoch,
            status="running",
            worktree_path=str(root / "old-tree"),
        )
        await store.save_attempt(attempt)
        if barrier == "dispatch_after":
            print("barrier", flush=True)
            await asyncio.Event().wait()
        save = process_observer(store, "crash", "target-1", owner.epoch)

        async def observe(observation: ProcessObservation) -> None:
            await save(observation)
            if observation.phase == "started" and barrier == "tool_after":
                print("barrier", flush=True)

        command = (
            "from pathlib import Path; "
            f"p=Path({str(root / 'marker')!r}); "
            "p.write_text(p.read_text()+'x' if p.exists() else 'x'); "
        )
        if barrier == "tool_after":
            command += (
                f"Path({str(root / 'ready')!r}).write_text('ready'); "
                f"Path({str(root / 'release')!r}).read_text(); "
                "p.write_text(p.read_text()+'y'); "
                f"Path({str(root / 'ready')!r}).write_text('finished')"
            )
        result = await LocalBackend(process_observer=observe).run_argv(
            [sys.executable, "-c", command],
            cwd=root,
            timeout=60,
            cancel=asyncio.Event(),
        )
        assert result.exit_code == 0
        if barrier == "ledger_before":
            print("barrier", flush=True)
            await asyncio.Event().wait()
        await store.save_attempt(
            attempt.model_copy(
                update={
                    "status": "completed",
                    "candidate_commit": "target-commit",
                }
            )
        )
        print("barrier", flush=True)
        await asyncio.Event().wait()


class MarkerExecutor:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def execute(
        self,
        run: WorkflowRun,
        task: WorkflowTask,
        attempt_id: str,
        predecessors: tuple[TaskAttempt, ...],
    ) -> TaskAttempt:
        result = await LocalBackend().run_argv(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; "
                f"p=Path({str(self.root / 'marker')!r}); "
                "p.write_text(p.read_text()+'x' if p.exists() else 'x')",
            ],
            cwd=self.root,
            timeout=10,
            cancel=asyncio.Event(),
        )
        assert result.exit_code == 0
        return TaskAttempt(
            run_id=run.run_id,
            task_id=task.task_id,
            attempt_id=attempt_id,
            owner_epoch=run.owner_epoch,
            status="completed",
            candidate_commit="graph-commit",
        )


async def service_worker(root: Path, barrier: str) -> None:
    from test_graph import ask, sink

    if barrier == "service_recover":
        events = await EventStore.open(root / "events.sqlite")
        provider = FakeProvider([])
        project = root / "source"
        turn = TurnRunner(
            store=events,
            provider=provider,
            registry=ToolRegistry([WriteTool()]),
            policy=PermissionPolicy(),
            sandbox=LocalBackend(),
            budget=Budget(),
            hooks=HookBus(),
            mode=BUILD,
            cwd=project,
            model="fake-model",
            system_prompt_builder=lambda cwd, mode: "",
        )
        service = WorkflowService(WorkflowStore(root / "workflow.sqlite"), root / "storage")
        context = WorkflowContext(
            "parent",
            project,
            turn,
            asyncio.Event(),
            sink,
            ask,
            True,
            (project / ".perdix/config.json").read_text(),
        )
        run_id = (root / "run-id").read_text()
        try:
            snapshot = await service.execute(
                WorkflowCommand(action="resume", run_id=run_id), context
            )
            head = await service._worktrees(context).git(
                project, "rev-parse", "HEAD", cancel=context.cancel
            )
            print(
                json.dumps(
                    {
                        "snapshot": snapshot.model_dump(),
                        "head": head,
                        "provider_calls": len(provider.requests),
                    }
                ),
                flush=True,
            )
        finally:
            await events.close()
        return
    provider = FakeProvider(
        [
            {
                "tool_uses": [
                    {
                        "id": "write",
                        "name": "write",
                        "input": {"path": "marker.txt", "content": "verified"},
                    }
                ]
            },
            {"text": "implemented"},
            {"text": '{"repair_tasks":[]}'},
        ]
    )
    service, context, events = await service_setup(root, provider)
    if barrier == "service_verify_live":
        import shlex

        command = (
            f"printf x >> {shlex.quote(str(root / 'verify-marker'))}; "
            f"printf ready > {shlex.quote(str(root / 'ready'))}; "
            f"cat {shlex.quote(str(root / 'release'))}; test -f marker.txt"
        )
        config = json.dumps({"verify": {"commands": [{"name": "marker", "run": command}]}})
        (context.project_root / ".perdix/config.json").write_text(config)
        context = replace(context, trusted_config_json=config)
        worktrees = service._worktrees(context)
        await worktrees.git(context.project_root, "add", ".", cancel=context.cancel)
        await worktrees.git(
            context.project_root, "commit", "-m", "verify barrier", cancel=context.cancel
        )
    planned = await service.plan(service_plan().model_dump_json(), context)
    (root / "run-id").write_text(planned.run.run_id)
    try:
        ready = await service.execute(
            WorkflowCommand(action="start", run_id=planned.run.run_id), context
        )
        assert ready.verification is not None and ready.verification.status == "passed"
        original = LocalBackend.run_argv

        async def gate(
            backend: LocalBackend,
            argv: Sequence[str],
            *,
            cwd: Path,
            timeout: float,
            cancel: asyncio.Event,
            env_extra: dict[str, str] | None = None,
        ) -> ExecResult:
            result = await original(
                backend, argv, cwd=cwd, timeout=timeout, cancel=cancel, env_extra=env_extra
            )
            if "merge" in argv and barrier == "service_accept_after":
                print("barrier", flush=True)
                await asyncio.Event().wait()
            return result

        append = WorkflowStore.append_event

        async def receipt_gate(store: WorkflowStore, event: WorkflowEvent) -> WorkflowEvent:
            result = await append(store, event)
            if (
                event.payload.kind == "acceptance"
                and event.payload.receipt.status == "accepted"
                and barrier == "service_accept_receipt"
            ):
                print("barrier", flush=True)
                await asyncio.Event().wait()
            return result

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(LocalBackend, "run_argv", gate)
            patch.setattr(WorkflowStore, "append_event", receipt_gate)
            await service.execute(
                WorkflowCommand(action="accept", run_id=planned.run.run_id), context
            )
    finally:
        await events.close()


async def accept_worker(root: Path, barrier: str, store: WorkflowStore) -> None:
    if barrier == "accept_recover":
        async with RunOwnership(store, "run", root / "locks"):
            candidate = CandidateCommit.model_validate_json((root / "candidate.json").read_text())
            worktrees = WorktreeService(LocalBackend(), root / "trees")
            integration = IntegrationService(
                worktrees,
                VerificationService(
                    worktrees,
                    PermissionPolicy({"bash": "allow"}),
                    allow,
                ),
            )
            recovered = await reconcile_acceptance(
                store,
                "run",
                integration,
                candidate,
                cancel=asyncio.Event(),
            )
            snapshot = await store.snapshot("run")
            head = await worktrees.git(
                Path(snapshot.run.project_root), "rev-parse", "HEAD", cancel=asyncio.Event()
            )
            print(
                json.dumps(
                    {"recovered": recovered, "head": head, "snapshot": snapshot.model_dump()}
                ),
                flush=True,
            )
        return
    scene = await setup(root)
    (root / "candidate.json").write_text(scene.candidate.model_dump_json())
    await store.create_run(scene.run.model_copy(update={"owner_epoch": 0, "status": "completed"}))
    async with RunOwnership(store, "run", root / "locks"):
        for payload in (
            VerificationRecorded(receipt=scene.receipt),
            AcceptanceRecorded(receipt=scene.intent),
        ):
            await store.append_event(
                WorkflowEvent(
                    run_id="run",
                    owner_epoch=1,
                    idempotency_key=payload.kind,
                    payload=payload,
                )
            )

        class BarrierBackend(LocalBackend):
            async def run_argv(
                self,
                argv: Sequence[str],
                *,
                cwd: Path,
                timeout: float,
                cancel: asyncio.Event,
                env_extra: dict[str, str] | None = None,
            ) -> ExecResult:
                merge = "merge" in argv
                if merge and barrier == "accept_before":
                    print("barrier", flush=True)
                    await asyncio.Event().wait()
                result = await super().run_argv(
                    argv,
                    cwd=cwd,
                    timeout=timeout,
                    cancel=cancel,
                    env_extra=env_extra,
                )
                if merge and barrier == "accept_after":
                    print("barrier", flush=True)
                    await asyncio.Event().wait()
                return result

        scene.service.worktrees.backend = BarrierBackend()
        await scene.service.accept(
            scene.run,
            scene.candidate,
            scene.receipt,
            intent=scene.intent,
            cancel=asyncio.Event(),
        )


@pytest.mark.parametrize(
    "barrier",
    [
        "dispatch_before",
        "dispatch_after",
        "tool_after",
        "ledger_before",
        "ledger_after",
        "checkpoint_before",
        "checkpoint_after",
    ],
)
def test_killed_owner_reconciles_without_replaying_shell(tmp_path: Path, barrier: str) -> None:
    # Given: 별도 프로세스가 실제 SQLite와 셸을 실행하다 지정 경계에서 대기한다.
    if barrier == "tool_after":
        os.mkfifo(tmp_path / "ready")
        os.mkfifo(tmp_path / "release")
    command = [sys.executable, str(Path(__file__).resolve()), str(tmp_path)]
    with subprocess.Popen(
        [*command, barrier],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        assert process.stdout is not None
        assert read_barrier(process.stdout) == "barrier"
        if barrier == "tool_after":
            assert read_fifo(tmp_path / "ready") == "ready"
        # When: owner를 SIGKILL하고 새 프로세스에서 소유권을 얻어 복구한다.
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=10)
        try:
            recovered = subprocess.run(
                [*command, "recover"],
                check=True,
                text=True,
                capture_output=True,
                timeout=15,
            )
            summary = json.loads(recovered.stdout)
            (tmp_path / "recovery.json").write_text(recovered.stdout)
            # Then: 완료 sibling은 보존되고 불명확한 셸은 재실행되지 않는다.
            finished = barrier in {"ledger_after", "checkpoint_before", "checkpoint_after"}
            expected_complete = ["sibling", "target"] if finished else ["sibling"]
            assert summary["result"]["completed_task_ids"] == expected_complete
            assert summary["result"]["resumable"] == (
                barrier
                in {
                    "dispatch_before",
                    "ledger_after",
                    "checkpoint_before",
                    "checkpoint_after",
                }
            )
            if barrier in {"tool_after", "ledger_before", "ledger_after"} or finished:
                assert (tmp_path / "marker").read_text() == "x"
            if barrier == "tool_after":
                observations = summary["snapshot"]["processes"]
                assert observations[0]["pid"] > 0
                assert observations[0]["start_identity"]
                assert observations[0]["phase"] == "started"
                os.kill(observations[0]["pid"], 0)
            print(
                json.dumps(
                    {
                        "barrier": barrier,
                        "result": summary["result"],
                        "artifact": str(tmp_path / "recovery.json"),
                    }
                )
            )
        finally:
            if barrier == "tool_after":
                (tmp_path / "release").write_text("exit")
                assert read_fifo(tmp_path / "ready") == "finished"
                assert (tmp_path / "marker").read_text() == "xy"


@pytest.mark.parametrize("barrier", ["accept_before", "accept_after"])
def test_acceptance_kill_reconciles_only_existing_git_mutation(
    tmp_path: Path,
    barrier: str,
) -> None:
    # Given: 실제 통합 후보와 검증 영수증, 영속 수락 의도를 만든 프로세스.
    command = [sys.executable, str(Path(__file__).resolve()), str(tmp_path)]
    with subprocess.Popen(
        [*command, barrier], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as process:
        assert process.stdout is not None
        assert read_barrier(process.stdout) == "barrier"
        # When: Git fast-forward 직전/직후 SIGKILL하고 새 프로세스에서 복구한다.
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=10)
    recovered = subprocess.run(
        [*command, "accept_recover"], check=True, capture_output=True, text=True, timeout=15
    )
    summary = json.loads(recovered.stdout)
    (tmp_path / "acceptance-recovery.json").write_text(recovered.stdout)
    # Then: 이전 의도만 재사용하며 복구 자체는 새 Git 반영을 시작하지 않는다.
    assert summary["recovered"] == (barrier == "accept_after")
    run = summary["snapshot"]["run"]
    assert summary["head"] == (
        run["candidate_commit"] if summary["recovered"] else run["base_commit"]
    )
    assert summary["snapshot"]["acceptance"]["receipt_id"] == "intent"
    assert summary["snapshot"]["acceptance"]["status"] == (
        "accepted" if summary["recovered"] else "awaiting"
    )
    print(
        json.dumps(
            {
                "barrier": barrier,
                "head": summary["head"],
                "recovered": summary["recovered"],
                "artifact": str(tmp_path / "acceptance-recovery.json"),
            }
        )
    )


@pytest.mark.asyncio
async def test_incompatible_graph_is_read_only(tmp_path: Path) -> None:
    # Given: 현재 실행기가 해석하지 못하는 graph version.
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    await store.create_run(run_model(tmp_path).model_copy(update={"graph_version": 99}))
    before = await store.snapshot("crash")
    # When: 복구 경계를 호출한다.
    result = await reconcile_run(store, "crash")
    # Then: 조회만 허용하며 이벤트나 상태를 수정하지 않는다.
    assert result.read_only and not result.resumable
    assert await store.snapshot("crash") == before


@pytest.mark.asyncio
async def test_cancel_during_started_observer_records_finished(tmp_path: Path) -> None:
    # Given: 시작 기록은 commit됐지만 관찰자 호출은 아직 반환되지 않은 실제 자식.
    store = WorkflowStore(tmp_path / "workflows.sqlite3")
    await store.create_run(run_model(tmp_path))
    attempt = TaskAttempt(
        run_id="crash", task_id="target", attempt_id="cancelled-1", owner_epoch=0, status="running"
    )
    await store.save_attempt(attempt)
    started = asyncio.Event()
    save = process_observer(store, "crash", attempt.attempt_id, 0)

    async def observe(observation: ProcessObservation) -> None:
        await save(observation)
        if observation.phase == "started":
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(
        LocalBackend(process_observer=observe).run_argv(
            [sys.executable, "-c", "import signal; signal.pause()"],
            cwd=tmp_path,
            timeout=30,
            cancel=asyncio.Event(),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=10)
    # When: 그 경계에서 협력 취소하고 실행기가 interrupted 결과를 기록한다.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await store.save_attempt(attempt.model_copy(update={"status": "interrupted"}))
    recovered = await reconcile_run(store, "crash")
    # Then: 자식 종료가 기록되어 명시적 재시도는 가능하다.
    snapshot = await store.snapshot("crash")
    assert snapshot.processes[0].phase == "finished"
    assert snapshot.processes[0].quiescent
    assert recovered.retryable_task_ids == ("target",)
    (tmp_path / "cancel-recovery.json").write_text(snapshot.model_dump_json())


@pytest.mark.parametrize(
    "barrier",
    [
        "service_accept_after",
        "service_accept_receipt",
        "service_verify_live",
    ],
)
def test_service_resume_after_real_process_kill(tmp_path: Path, barrier: str) -> None:
    # Given: 서비스의 실제 하네스·검증·수락 경로를 실행하는 별도 프로세스.
    if barrier == "service_verify_live":
        os.mkfifo(tmp_path / "ready")
        os.mkfifo(tmp_path / "release")
    command = [sys.executable, str(Path(__file__).resolve()), str(tmp_path)]
    with subprocess.Popen(
        [*command, barrier], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as process:
        assert process.stdout is not None
        if barrier == "service_verify_live":
            assert read_fifo(tmp_path / "ready") == "ready"
        else:
            assert read_barrier(process.stdout) == "barrier"
        # When: 서버를 종료한 후 새 프로세스에서 서비스 resume을 호출한다.
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=10)
    try:
        recovered = subprocess.run(
            [*command, "service_recover"], check=True, capture_output=True, text=True, timeout=30
        )
        summary = json.loads(recovered.stdout)
        (tmp_path / "service-recovery.json").write_text(recovered.stdout)
        # Then: 부작용 불명확 단계는 차단하고 Git 반영 완료는 의도로 대조한다.
        assert summary["provider_calls"] == 0
        run = summary["snapshot"]["run"]
        if barrier == "service_verify_live":
            assert run["status"] == "blocked"
            assert (tmp_path / "verify-marker").read_text() == "x"
            assert summary["head"] == run["base_commit"]
        else:
            assert run["acceptance_status"] == "accepted"
            assert summary["head"] == run["candidate_commit"]
        print(
            json.dumps(
                {
                    "barrier": barrier,
                    "status": run["status"],
                    "acceptance": run["acceptance_status"],
                    "head": summary["head"],
                    "artifact": str(tmp_path / "service-recovery.json"),
                }
            )
        )
    finally:
        if barrier == "service_verify_live":
            (tmp_path / "release").write_text("exit")


if __name__ == "__main__":
    asyncio.run(worker(Path(sys.argv[1]), sys.argv[2]))
