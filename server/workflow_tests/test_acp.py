"""실제 stdio 서버에서 계획·표준 업데이트·재접속을 관측한다."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from acp import RequestError, text_block

from perdix.config.schema import ServerConfig
from perdix.gateway.config import GatewayConfig, ProjectConfig
from perdix.gateway.events import GatewayStore
from perdix.gateway.runtime import GatewayRuntime, SubmitRequest, WorkflowBusyError
from perdix.workflows.store import WorkflowStore


def prepare(tmp_path: Path) -> GatewayConfig:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-b", "main"], ["config", "user.name", "Workflow Test"],
                 ["config", "user.email", "workflow@example.invalid"],
                 ["commit", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    script = tmp_path / "fake.json"
    script.write_text(json.dumps([{"text": "ordinary reply", "stop": "end_turn"}]))
    return GatewayConfig(db_path=tmp_path / "gateway.db",
        projects={"test": ProjectConfig(cwd=repo, trust_project=True)},
        server=ServerConfig(provider="fake", fake_script=script,
            db_path=str(tmp_path / "sessions.db"), credentials_path=tmp_path / "credentials.json"))


async def idle(runtime: GatewayRuntime, job_id: str) -> None:
    async with asyncio.timeout(20):
        while job_id in runtime.tasks:
            for permission_id, pending in list(runtime.permissions.items()):
                if pending.job_id == job_id and not pending.future.done():
                    runtime.approve(job_id, permission_id, "allow_once")
            await asyncio.sleep(0.02)


async def record(runtime: GatewayRuntime, name: str) -> None:
    directory = Path(__file__).resolve().parents[2] / ".omo/evidence/verified-team-workflows"
    directory.mkdir(parents=True, exist_ok=True)
    events = []
    for job in await runtime.store.jobs():
        events.extend(event.model_dump(mode="json", by_alias=True)
                      for event in await runtime.store.events(job.id))
    (directory / f"task-9-{name}.json").write_text(json.dumps(events, ensure_ascii=False, indent=2))


@pytest.mark.asyncio
async def test_stdio_natural_plan_outside_git_has_actionable_error(tmp_path: Path) -> None:
    config = prepare(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    runtime = GatewayRuntime(config, GatewayStore(config.db_path))
    await runtime.start()
    try:
        plan = {"objective": "inspect", "tasks": [{"taskId": "inspect",
            "title": "inspect", "role": "implement", "model": "fake-model", "paths": ["."],
            "acceptance": ["report"]}]}
        job = await runtime.submit(SubmitRequest(
            project="test", prompt="/workflow plan " + json.dumps(plan),
        ))
        await idle(runtime, job.id)
        conn, _ = runtime.connections[job.id]
        session = await conn.new_session(cwd=str(outside), mcp_servers=[])
        with pytest.raises(RequestError, match="Git 저장소.*일반 프롬프트") as failure:
            await conn.prompt(session_id=session.session_id, prompt=[text_block(
                "/workflow  plan 이 프로젝트의 구조에서 codex v1과 v2의 구조적 차이를 분석해줘. "
                "그리고 알려진 이슈를 해결할 방법을 알려줘",
            )])
        assert failure.value.code == -32602
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_stdio_workflow_binding_status_restart_and_standard_fallback(tmp_path: Path) -> None:
    config = prepare(tmp_path)
    runtime = GatewayRuntime(config, GatewayStore(config.db_path))
    await runtime.start()
    try:
        plan = {"objective": "inspect repository", "tasks": [{"taskId": "inspect",
            "title": "inspect", "role": "implement", "model": "fake-model", "paths": ["."],
            "acceptance": ["report state"]}]}
        job = await runtime.submit(SubmitRequest(project="test",
            prompt="/workflow plan " + json.dumps(plan)))
        await idle(runtime, job.id)
        bound = await runtime.store.get(job.id)
        assert bound is not None and bound.workflow_run_id and bound.session_id
        assert bound.status == "completed", bound.result
        conn, session_id = runtime.connections[job.id]
        response = await conn.prompt(session_id=session_id,
            prompt=[text_block(f"/workflow status {bound.workflow_run_id}")])
        assert response.stop_reason == "end_turn"
        events = await runtime.store.events(job.id)
        updates = [event.data for event in events if event.kind == "update"]
        assert any(isinstance(update, dict) and update.get("sessionUpdate") == "plan"
                   and "_meta" in update for update in updates)
        assert "Workflow " in "".join(runtime.clients[job.id].text)
        async with runtime.project_locks["test"]:
            with pytest.raises(WorkflowBusyError):
                await runtime.control(job.id, "reject")
        assert config.server.fake_script is not None
        model_command = f"/workflow accept {bound.workflow_run_id}"
        config.server.fake_script.write_text(json.dumps([{"text": model_command}]))
        ordinary = await runtime.submit(SubmitRequest(project="test", prompt="hello"))
        await idle(runtime, ordinary.id)
        ordinary_result = await runtime.store.get(ordinary.id)
        assert ordinary_result is not None and ordinary_result.result == model_command
        unchanged = await WorkflowStore(tmp_path / "workflows.sqlite3").snapshot(
            bound.workflow_run_id
        )
        assert unchanged.run.acceptance_status == "awaiting"
    finally:
        await record(runtime, "stdio-" + tmp_path.name)
        await runtime.close()
    restarted = GatewayRuntime(config, GatewayStore(config.db_path))
    await restarted.start()
    try:
        await restarted.control(job.id, "reject")
        await idle(restarted, job.id)
        reloaded = await restarted.store.get(job.id)
        assert reloaded is not None and reloaded.status == "completed", reloaded
        assert reloaded.session_id == bound.session_id
        assert reloaded.workflow_run_id == bound.workflow_run_id
        store = WorkflowStore(tmp_path / "workflows.sqlite3")
        runs = await store.list_runs(str(config.projects["test"].cwd))
        assert len(runs) == 1
        assert runs[0].acceptance_status == "rejected"
    finally:
        await record(restarted, "restart")
        await restarted.close()


@pytest.mark.asyncio
async def test_stdio_start_accept_verifies_candidate_before_changing_head(tmp_path: Path) -> None:
    config = prepare(tmp_path)
    root = config.projects["test"].cwd
    (root / ".perdix").mkdir()
    (root / ".perdix/config.json").write_text(json.dumps({"verify": {"commands": [
        {"name": "marker", "run": "test -f marker.txt"}]}}))
    for arguments in (["add", "."], ["commit", "-m", "verification"]):
        subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)
    assert config.server.fake_script is not None
    config.server.fake_script.write_text(json.dumps([
        {"tool_uses": [{"id": "write", "name": "write", "input": {
            "path": "marker.txt", "content": "verified"}}]},
        {"text": "implemented"}, {"text": '{"repair_tasks":[]}'}]))
    runtime = GatewayRuntime(config, GatewayStore(config.db_path))
    await runtime.start()
    try:
        plan = {"objective": "write marker", "tasks": [{"taskId": "write",
            "title": "write marker", "role": "implement", "model": "fake-model",
            "paths": ["marker.txt"], "acceptance": ["marker exists"]}]}
        job = await runtime.submit(SubmitRequest(project="test",
            prompt="/workflow plan " + json.dumps(plan)))
        await idle(runtime, job.id)
        bound = await runtime.store.get(job.id)
        assert bound is not None and bound.workflow_run_id
        conn = runtime.connections[job.id]
        await conn[0].set_session_mode(session_id=conn[1], mode_id="chat")
        await runtime.control(job.id, "start")
        with pytest.raises(WorkflowBusyError):
            await runtime.control(job.id, "start")
        await idle(runtime, job.id)
        store = WorkflowStore(tmp_path / "workflows.sqlite3")
        snapshot = await store.snapshot(bound.workflow_run_id)
        assert snapshot.run.status == "completed", snapshot
        assert snapshot.run.verification_status == "passed", snapshot
        assert not (root / "marker.txt").exists()
        assert runtime.connections[job.id] == conn
        await runtime.control(job.id, "accept")
        await idle(runtime, job.id)
        accepted = await store.snapshot(bound.workflow_run_id)
        assert accepted.run.acceptance_status == "accepted", accepted
        assert (root / "marker.txt").read_text() == "verified"
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()
        assert head == accepted.run.candidate_commit
        loaded = await conn[0].load_session(session_id=conn[1], cwd=str(root), mcp_servers=[])
        assert loaded.modes is not None and loaded.modes.current_mode_id == "chat"
    finally:
        await record(runtime, "stdio-" + tmp_path.name)
        await runtime.close()


@pytest.mark.asyncio
async def test_stdio_cancel_running_tool_and_resume_same_run(tmp_path: Path) -> None:
    config = prepare(tmp_path)
    assert config.server.fake_script is not None
    config.server.fake_script.write_text(json.dumps([
        {"tool_uses": [{"id": "slow", "name": "bash", "input": {"command": "sleep 30"}}]},
        {"text": "stopped"}, {"text": "resumed"}, {"text": '{"repair_tasks":[]}'}]))
    runtime = GatewayRuntime(config, GatewayStore(config.db_path))
    await runtime.start()
    try:
        plan = {"objective": "cancel task", "tasks": [{"taskId": "slow",
            "title": "slow command", "role": "implement", "model": "fake-model",
            "paths": ["."], "acceptance": ["finish command"]}]}
        job = await runtime.submit(SubmitRequest(project="test",
            prompt="/workflow plan " + json.dumps(plan)))
        await idle(runtime, job.id)
        bound = await runtime.store.get(job.id)
        assert bound is not None and bound.workflow_run_id
        await runtime.control(job.id, "start")
        async with asyncio.timeout(15):
            while not runtime.permissions:
                assert job.id in runtime.tasks
                await asyncio.sleep(0.02)
        for permission_id in list(runtime.permissions):
            runtime.approve(job.id, permission_id, "allow_once")
        async with asyncio.timeout(15):
            while True:
                events = await runtime.store.events(job.id)
                if any(isinstance(event.data, dict)
                       and event.data.get("toolCallId") == "slow"
                       and event.data.get("status") == "in_progress" for event in events):
                    break
                await asyncio.sleep(0.02)
        await runtime.cancel(job.id)
        assert job.id not in runtime.tasks
        cancelled = await runtime.store.get(job.id)
        assert cancelled is not None and cancelled.status == "cancelled"
        store = WorkflowStore(tmp_path / "workflows.sqlite3")
        before = await store.snapshot(bound.workflow_run_id)
        assert before.run.status in {"cancelled", "interrupted", "blocked"}
        await runtime.control(job.id, "resume")
        await idle(runtime, job.id)
        after = await store.snapshot(bound.workflow_run_id)
        assert after.run.run_id == before.run.run_id
        assert after.run.session_id == before.run.session_id
        assert after.seq > before.seq
        assert len(await store.list_runs(str(config.projects["test"].cwd))) == 1
        await runtime.control(job.id, "retry", "slow")
        await idle(runtime, job.id)
        retried = await store.snapshot(bound.workflow_run_id)
        assert len(retried.attempts) > len(after.attempts)
        assert retried.run.verification_status == "unverified"
        assert retried.run.acceptance_status == "awaiting"
    finally:
        await record(runtime, "stdio-" + tmp_path.name)
        await runtime.close()
