"""실제 SQLite·프로세스로 실행 소유권과 취소 경계를 검증한다."""
from __future__ import annotations

import asyncio
import os
import shlex
import sys
from pathlib import Path

import pytest
from filelock import Timeout

from perdix.harness.budget import BudgetExceeded
from perdix.sandbox.local import LocalBackend
from perdix.workflows.execution_context import ExecutionContext
from perdix.workflows.models import WorkflowPlan, WorkflowRun, WorkflowTask
from perdix.workflows.ownership import RunOwnership
from perdix.workflows.store import WorkflowOwnerError, WorkflowStore


def run_record(root: Path, steps: int = 200) -> WorkflowRun:
    return WorkflowRun(
        run_id="run", session_id="session", project_root=str(root),
        base_commit="base", target_branch="main",
        plan=WorkflowPlan(objective="test", shared_steps_limit=steps, tasks=(WorkflowTask(
            task_id="a", title="task", role="implement", model="fake", paths=("a",),
            acceptance=("pass",),
        ),)),
    )


@pytest.mark.asyncio
async def test_other_os_process_cannot_acquire_same_run(tmp_path: Path) -> None:
    # Given: 별도 OS 프로세스가 run의 FileLock과 epoch를 소유한다.
    code = '''
import asyncio, sys
from pathlib import Path
from perdix.workflows.store import WorkflowStore
from perdix.workflows.ownership import RunOwnership
async def main():
    store = WorkflowStore(Path(sys.argv[1]))
    await store.open()
    async with RunOwnership(store, "run", Path(sys.argv[2])):
        print("owned", flush=True)
        await asyncio.to_thread(sys.stdin.readline)
    await store.close()
asyncio.run(main())
'''
    store = WorkflowStore(tmp_path / "ledger.db")
    await store.open()
    await store.create_run(run_record(tmp_path))
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code, str(tmp_path / "ledger.db"), str(tmp_path / "locks"),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    )
    assert child.stdout is not None and child.stdin is not None
    try:
        assert await asyncio.wait_for(child.stdout.readline(), 10) == b"owned\n"
        # When / Then: 두 번째 프로세스의 동일 run 소유권 획득은 실패한다.
        with pytest.raises(Timeout):
            async with RunOwnership(store, "run", tmp_path / "locks"):
                pytest.fail("duplicate owner")
    finally:
        child.stdin.write(b"release\n")
        await child.stdin.drain()
        await asyncio.wait_for(child.wait(), 10)
        await store.close()
    assert child.returncode == 0


@pytest.mark.asyncio
async def test_shared_reservations_persist_across_resume(tmp_path: Path) -> None:
    # Given: 전체 실행 한도는 2이며 공급자 호출 전에 예약한다.
    store = WorkflowStore(tmp_path / "ledger.db")
    await store.open()
    await store.create_run(run_record(tmp_path, 2))
    calls: list[int] = []
    try:
        async with RunOwnership(store, "run", tmp_path / "locks") as owner:
            context = ExecutionContext(owner, asyncio.Event())

            async def provider() -> None:
                await context.reserve_step()
                calls.append(1)

            await asyncio.gather(provider(), provider())
        # When: 새 owner에서 이어서 공급자를 호출한다.
        async with RunOwnership(store, "run", tmp_path / "locks") as owner:
            context = ExecutionContext(owner, asyncio.Event())
            with pytest.raises(BudgetExceeded):
                await provider()
        # Then: 재개로 카운터가 초기화되지 않아 세 번째 호출은 실행되지 않는다.
        assert len(calls) == 2
        assert (await store.snapshot("run")).steps_used == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cancel_waiting_permission_and_child_process(tmp_path: Path) -> None:
    # Given: 승인 하나가 열려 있고 두 번째 승인 및 실제 child가 대기한다.
    store = WorkflowStore(tmp_path / "ledger.db")
    await store.open()
    await store.create_run(run_record(tmp_path))
    cancel = asyncio.Event()
    entered = asyncio.Event()
    second_called = False
    # readiness는 파일 polling 대신 자식의 TCP 접속 이벤트를 사용한다.
    ready = asyncio.Event()
    pid: list[int] = []

    async def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        pid.append(int(await reader.readline()))
        ready.set()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(connected, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    script = (
        "import os,socket,time; "
        f"s=socket.create_connection(('127.0.0.1',{port})); "
        "s.sendall((str(os.getpid())+'\\n').encode()); s.close(); time.sleep(60)"
    )
    try:
        async with RunOwnership(store, "run", tmp_path / "locks") as owner:
            context = ExecutionContext(owner, cancel)

            async def permission() -> None:
                entered.set()
                await asyncio.Event().wait()

            async def second() -> None:
                nonlocal second_called
                second_called = True

            first = asyncio.create_task(context.permission(permission))
            await entered.wait()
            waiting = asyncio.create_task(context.permission(second))
            child = asyncio.create_task(LocalBackend().run(
                f"exec {shlex.quote(sys.executable)} -c {shlex.quote(script)}",
                cwd=tmp_path, timeout=20, cancel=cancel,
            ))
            await asyncio.wait_for(ready.wait(), 10)
            # When: 부모가 취소한다.
            cancel.set()
            results = await asyncio.wait_for(
                asyncio.gather(first, waiting, return_exceptions=True), 10,
            )
            result = await asyncio.wait_for(child, 10)
            # Then: 대기 승인은 열리지 않으며 실제 PID도 종료된다.
            assert all(isinstance(item, asyncio.CancelledError) for item in results)
            assert not second_called and result.cancelled
            with pytest.raises(ProcessLookupError):
                os.kill(pid[0], 0)
    finally:
        server.close()
        await server.wait_closed()
        await store.close()


@pytest.mark.asyncio
async def test_late_owner_result_is_rejected(tmp_path: Path) -> None:
    # Given: epoch 1에서 작업을 시작한다.
    store = WorkflowStore(tmp_path / "ledger.db")
    await store.open()
    await store.create_run(run_record(tmp_path))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def work() -> str:
        entered.set()
        await release.wait()
        return "late result"

    try:
        with pytest.raises(WorkflowOwnerError):
            async with RunOwnership(store, "run", tmp_path / "locks") as owner:
                context = ExecutionContext(owner, asyncio.Event())
                running = asyncio.create_task(context.run(work))
                await entered.wait()
                # When: 영속 fence가 바뀐 뒤 이전 작업이 결과를 반환한다.
                await store.claim_owner("run", "replacement")
                release.set()
                # Then: 늦은 결과는 성공으로 전달되지 않는다.
                with pytest.raises(WorkflowOwnerError):
                    await running
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_wall_budget_persists_across_owner_resume(tmp_path: Path) -> None:
    # Given: 이전 owner가 전체 활성 시간 예산을 소진했다.
    store = WorkflowStore(tmp_path / "ledger.db")
    await store.open()
    await store.create_run(run_record(tmp_path))
    epoch = await store.claim_owner("run", "first")
    await store.finish_owner("run", epoch, 1800)
    try:
        async with RunOwnership(store, "run", tmp_path / "locks") as owner:
            context = ExecutionContext(owner, asyncio.Event())
            # When / Then: 새 owner도 공급자 실행 전에 거절된다.
            with pytest.raises(BudgetExceeded):
                await context.reserve_step()
            assert (await store.snapshot("run")).steps_used == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_task_slots_limit_concurrency_to_two(tmp_path: Path) -> None:
    # Given: 동시에 실행하려는 작업 10개가 같은 context를 공유한다.
    store = WorkflowStore(tmp_path / "ledger.db")
    await store.open()
    await store.create_run(run_record(tmp_path))
    active = 0
    peak = 0
    two_active = asyncio.Event()
    release = asyncio.Event()
    try:
        async with RunOwnership(store, "run", tmp_path / "locks") as owner:
            context = ExecutionContext(owner, asyncio.Event())

            async def work() -> None:
                nonlocal active, peak
                async with context.task_slot():
                    active += 1
                    peak = max(peak, active)
                    if active == 2:
                        two_active.set()
                    await release.wait()
                    active -= 1

            # When: 슬롯을 경합시키고 첫 두 작업을 해제한다.
            workers = [asyncio.create_task(work()) for _ in range(10)]
            await asyncio.wait_for(two_active.wait(), 5)
            release.set()
            await asyncio.gather(*workers)
            # Then: 최대 두 작업만 실행 구간에 진입한다.
            assert peak == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_released_owner_cannot_start_side_effect(tmp_path: Path) -> None:
    # Given: 소유권 context가 정상적으로 종료되었다.
    store = WorkflowStore(tmp_path / "ledger.db")
    await store.open()
    await store.create_run(run_record(tmp_path))
    async with RunOwnership(store, "run", tmp_path / "locks") as owner:
        context = ExecutionContext(owner, asyncio.Event())
    invoked = False

    async def side_effect() -> None:
        nonlocal invoked
        invoked = True

    try:
        # When / Then: 다음 owner가 없어도 해제된 소유권으로 실행하지 않는다.
        with pytest.raises(WorkflowOwnerError):
            await context.run(side_effect)
        assert not invoked
    finally:
        await store.close()
