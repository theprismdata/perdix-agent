from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import pytest
from acp import PROTOCOL_VERSION
from acp.helpers import text_block
from acp.schema import ClientCapabilities, RequestPermissionResponse
from acp.stdio import spawn_agent_process

from perdix.permissions.policy import PermissionPolicy
from perdix.teams.models import TeamCommand, TeamError
from perdix.teams.store import TeamStore


def member(root: Path, name: str, role: str = "member", project: Path | None = None) -> TeamStore:
    return TeamStore(root / "teams.db", project or root, "demo", name, f"session-{name}",
                     "leader" if role == "leader" else "member")


@pytest.mark.asyncio
async def test_messages_are_fifo_and_receipts_do_not_delete_history(tmp_path: Path) -> None:
    leader, worker = member(tmp_path, "lead", "leader"), member(tmp_path, "worker")
    await leader.join()
    await worker.join()
    await leader.command(TeamCommand(action="send", recipient="worker", body="first"))
    await leader.command(TeamCommand(action="send", recipient="worker", body="second"))
    messages = await worker.pending()
    assert [m.body for m in messages] == ["first", "second"]
    await worker.acknowledge(messages)
    assert await worker.pending() == []
    async with worker.transaction() as db:
        async with db.execute("SELECT COUNT(*) FROM messages WHERE recipient=?",
                              (worker.session,)) as cursor:
            assert (await cursor.fetchone())[0] == 2


@pytest.mark.asyncio
async def test_only_leader_assigns_and_only_owner_completes(tmp_path: Path) -> None:
    leader, worker, reviewer = (member(tmp_path, "lead", "leader"), member(tmp_path, "worker"),
                                member(tmp_path, "reviewer"))
    for actor in (leader, worker, reviewer):
        await actor.join()
    assignment = TeamCommand(action="assign", task_id="fix", recipient="worker", body="fix it")
    with pytest.raises(TeamError):
        await worker.command(assignment)
    await leader.command(assignment)
    with pytest.raises(TeamError):
        await reviewer.command(TeamCommand(action="complete", task_id="fix", body="wrong"))
    await worker.command(TeamCommand(action="complete", task_id="fix", body="verified"))
    status = json.loads(await leader.command(TeamCommand(action="status")))
    assert status["tasks"][0]["state"] == "done"
    assert (await leader.pending())[0].sender == "worker"


@pytest.mark.asyncio
async def test_overlapping_file_assignments_roll_back_message_delivery(tmp_path: Path) -> None:
    leader, worker = member(tmp_path, "lead", "leader"), member(tmp_path, "worker")
    await leader.join()
    await worker.join()
    await leader.command(TeamCommand(action="assign", task_id="a", recipient="worker",
                                    body="edit", paths=["src"]))
    with pytest.raises(TeamError):
        await leader.command(TeamCommand(action="assign", task_id="b", recipient="worker",
                                        body="edit", paths=["src/main.py"]))
    assert len(await worker.pending()) == 1


@pytest.mark.asyncio
async def test_same_team_name_in_different_projects_cannot_communicate(tmp_path: Path) -> None:
    leader = member(tmp_path, "lead", "leader")
    foreign = member(tmp_path, "foreign", "leader", tmp_path / "other")
    await leader.join()
    await foreign.join()
    with pytest.raises(TeamError):
        await leader.command(TeamCommand(action="send", recipient="foreign", body="secret"))
    assert await foreign.pending() == []


@pytest.mark.asyncio
async def test_replaced_member_does_not_receive_old_messages(tmp_path: Path) -> None:
    leader, worker = member(tmp_path, "lead", "leader"), member(tmp_path, "worker")
    await leader.join()
    await worker.join()
    await leader.command(TeamCommand(action="send", recipient="worker", body="old"))
    await leader.command(TeamCommand(action="remove", recipient="worker"))
    replacement = TeamStore(worker.path, tmp_path, "demo", "worker", "replacement", "member")
    await replacement.join()
    assert await replacement.pending() == []
    with pytest.raises(TeamError):
        await worker.pending()


@pytest.mark.asyncio
async def test_concurrent_join_rejects_duplicate_names(tmp_path: Path) -> None:
    leader = member(tmp_path, "lead", "leader")
    await leader.join()
    workers = [TeamStore(leader.path, tmp_path, "demo", "worker", str(i), "member")
               for i in range(2)]
    results = await asyncio.gather(*(w.join() for w in workers), return_exceptions=True)
    assert sum(isinstance(result, TeamError) for result in results) == 1


def test_team_coordination_does_not_grant_file_execution_permissions() -> None:
    policy = PermissionPolicy(mode="deny")
    assert policy.evaluate("team", {"action": "send"}) == "allow"
    assert policy.evaluate("bash", {"command": "true"}) == "deny"
    assert policy.evaluate("write", {"path": "a"}) == "deny"


class Client:
    def __init__(self) -> None:
        self.updates: list[Any] = []

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append(update)

    async def request_permission(self, **kwargs: Any) -> RequestPermissionResponse:
        return RequestPermissionResponse.model_validate({"outcome": {"outcome": "cancelled"}})


@pytest.mark.asyncio
async def test_independent_acp_servers_wake_report_and_stop(tmp_path: Path) -> None:
    leader_script = tmp_path / "leader.json"
    worker_script = tmp_path / "worker.json"
    leader_script.write_text("[]", encoding="utf-8")
    worker_script.write_text(json.dumps([
        {"tool_uses": [{"id": "complete", "name": "team", "input": {
            "action": "complete", "task_id": "fix", "body": "verified",
        }}]}, {"text": "finished"},
    ]), encoding="utf-8")
    async with AsyncExitStack() as stack:
        connections = []
        clients = []
        for name, role, script in (("lead", "leader", leader_script),
                                   ("worker", "member", worker_script)):
            client = Client()
            clients.append(client)
            args = ["-m", "perdix", "--provider", "fake", "--fake-script", str(script),
                    "--db", str(tmp_path / f"{name}.db"), "--team-db", str(tmp_path / "teams.db"),
                    "--team", "demo", "--member", name, "--team-role", role,
                    "--permission-mode", "deny"]
            if role == "member":
                args.append("--team-worker")
            connection, process = await stack.enter_async_context(spawn_agent_process(
                client, sys.executable, *args, use_unstable_protocol=True,
            ))
            await connection.initialize(protocol_version=PROTOCOL_VERSION,
                                        client_capabilities=ClientCapabilities())
            session = await connection.new_session(cwd=str(tmp_path), mcp_servers=[])
            await connection.prompt(session_id=session.session_id,
                                    prompt=[text_block("/team status")])
            connections.append((connection, session.session_id, process))
        leader = TeamStore(tmp_path / "teams.db", tmp_path, "demo", "lead",
                           connections[0][1], "leader")
        worker_conn, worker_id, _ = connections[1]
        waiting = asyncio.create_task(worker_conn.prompt(session_id=worker_id,
                                                        prompt=[text_block("")]))
        await leader.command(TeamCommand(action="assign", task_id="fix", recipient="worker",
                                        body="verify task", paths=["a.py"]))
        response = await asyncio.wait_for(waiting, timeout=10)
        assert response.stop_reason == "end_turn"
        status = json.loads(await leader.command(TeamCommand(action="status")))
        assert status["tasks"][0]["state"] == "done"
        assert any(m.body.endswith("verified") for m in await leader.pending())
        waiting = asyncio.create_task(worker_conn.prompt(session_id=worker_id,
                                                        prompt=[text_block("")]))
        await leader.command(TeamCommand(action="stop"))
        assert (await asyncio.wait_for(waiting, timeout=10)).stop_reason == "cancelled"


@pytest.mark.asyncio
async def test_cli_workers_repeat_prompts_and_exit_on_team_stop(tmp_path: Path) -> None:
    cli = Path(__file__).resolve().parents[2] / "packages/cli/dist/main.js"
    assert cli.is_file(), "먼저 pnpm -r build를 실행하세요"
    node = shutil.which("node")
    assert node is not None
    scripts = {
        "lead": [{"text": "leader-ready"}, {"text": "worker-joined"}, {"tool_uses": [{
            "id": "stop", "name": "team", "input": {"action": "stop"},
        }]}, {"text": "team-finished"}],
        "worker": [{"tool_uses": [{"id": "complete", "name": "team", "input": {
            "action": "complete", "task_id": "fix", "body": "cli-verified",
        }}]}, {"text": "worker-finished"}],
    }
    processes: list[asyncio.subprocess.Process] = []
    try:
        for name, responses in scripts.items():
            script = tmp_path / f"{name}.json"
            script.write_text(json.dumps(responses), encoding="utf-8")
            server_command = (
                f"{sys.executable} -m perdix --provider fake --fake-script {script} "
                f"--db {tmp_path / (name + '.db')}"
            )
            args = [node, str(cli), "--server", server_command, "--cwd", str(tmp_path),
                    "--tui", "hermes",
                    "--team", "demo", "--member", name, "--team-worker", "--json",
                    "--team-db", str(tmp_path / "teams.db")]
            if name == "lead":
                args += ["--team-role", "leader", "-p", "start"]
            process = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HOME": str(tmp_path / "home")},
            )
            processes.append(process)
            assert process.stdout is not None
            async with asyncio.timeout(10):
                while True:
                    line = await process.stdout.readline()
                    assert line, "CLI가 대기 상태에 도달하지 못했습니다"
                    event = json.loads(line)
                    if event.get("type") == "agent_text" and "기다립니다" in event.get("text", ""):
                        break
        assert processes[0].stdout is not None
        async with asyncio.timeout(10):
            while True:
                line = await processes[0].stdout.readline()
                assert line
                event = json.loads(line)
                if event.get("type") == "agent_text" and "기다립니다" in event.get("text", ""):
                    break
        async with member(tmp_path, "lead", "leader").transaction() as db:
            async with db.execute("SELECT session FROM members WHERE name='lead'") as cursor:
                row = await cursor.fetchone()
        assert row is not None
        leader = TeamStore(tmp_path / "teams.db", tmp_path, "demo", "lead", row[0], "leader")
        await leader.command(TeamCommand(action="assign", task_id="fix", recipient="worker",
                                        body="finish and report", paths=["a.py"]))
        outputs = await asyncio.wait_for(
            asyncio.gather(*(process.communicate() for process in processes)), timeout=15,
        )
        assert [p.returncode for p in processes] == [0, 0], outputs
        assert b"team-finished" in outputs[0][0]
        assert b"worker-finished" in outputs[1][0]
        async with leader.transaction() as db:
            async with db.execute("SELECT COUNT(*) FROM members WHERE active=1") as cursor:
                assert (await cursor.fetchone())[0] == 0
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
            await process.wait()


@pytest.mark.asyncio
async def test_waiting_cli_worker_cancels_and_releases_name(tmp_path: Path) -> None:
    leader = member(tmp_path, "lead", "leader")
    await leader.join()
    cli = Path(__file__).resolve().parents[2] / "packages/cli/dist/main.js"
    script = tmp_path / "empty.json"
    script.write_text("[]", encoding="utf-8")
    node = shutil.which("node")
    assert node is not None
    process = await asyncio.create_subprocess_exec(
        node, str(cli), "--server",
        f"{sys.executable} -m perdix --provider fake --fake-script {script} "
        f"--db {tmp_path / 'worker.db'}",
        "--cwd", str(tmp_path), "--team", "demo", "--member", "worker", "--team-worker",
        "--team-db", str(tmp_path / "teams.db"), "--json",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "HOME": str(tmp_path / "home")},
    )
    try:
        assert process.stdout is not None
        async with asyncio.timeout(10):
            while True:
                line = await process.stdout.readline()
                assert line
                event = json.loads(line)
                if event.get("type") == "agent_text" and "기다립니다" in event.get("text", ""):
                    break
        process.send_signal(signal.SIGINT)
        output, errors = await asyncio.wait_for(process.communicate(), timeout=10)
        assert process.returncode == 0, errors
        assert b'"stopReason":"cancelled"' in output
        replacement = member(tmp_path, "worker")
        await replacement.join()
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


@pytest.mark.parametrize("surface", ["default", "hermes"])
@pytest.mark.asyncio
async def test_tui_team_command_reaches_session_prompt(tmp_path: Path, surface: str) -> None:
    cli_root = Path(__file__).resolve().parents[2] / "packages/cli"
    node = shutil.which("node")
    assert node is not None
    script = """
import assert from 'node:assert/strict';
import React from 'react';
import { render } from 'ink-testing-library';
import { App } from './src/ui/App.tsx';
import { HermesAdapter } from './src/hermes/adapter.ts';
const prompts = [];
let resolveReady;
const ready = new Promise(resolve => {resolveReady = resolve;});
const handle = {
  info: {sessionId:'qa',cwd:process.cwd(),currentMode:'general',
    modes:[],currentModel:null,models:[]},
  prompt: async text => {prompts.push(text); return {stopReason:'end_turn'};},
  onEvent: () => {resolveReady(); return () => {};}, onPermission: () => () => {},
  cancel:async()=>{},close:async()=>{},setMode:async()=>{},setModel:async()=>{},
};
if (process.argv[1] === 'hermes') {
  const adapter = new HermesAdapter({newSession:async()=>handle}, {cwd:process.cwd()});
  await adapter.request('session.create', {});
  const catalog = await adapter.request('commands.catalog', {session_id:'qa'});
  assert.ok(catalog.pairs.some(pair => pair[0] === '/team'));
  const result = await adapter.request('slash.exec', {session_id:'qa',command:'/team status'});
  assert.equal(result.type, 'send');
  assert.equal(result.message, '/team status');
} else {
  const app = render(React.createElement(App, {handle}));
  try {
    const frame = () => new Promise(resolve => {
      const write = app.stdout.write;
      app.stdout.write = value => {
        write(value);
        app.stdout.write = write;
        resolve();
      };
    });
    await ready;
    let rendered = frame();
    app.stdin.write('/team status');
    await rendered;
    rendered = frame();
    app.stdin.write('\\r');
    await rendered;
    assert.deepEqual(prompts, ['/team status']);
  } finally {app.unmount(); app.cleanup();}
}
"""
    process = await asyncio.create_subprocess_exec(
        node, "--import", "tsx", "--input-type=module", "-e", script, surface,
        cwd=cli_root, env={**os.environ, "HOME": str(tmp_path / "home")},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    output, errors = await asyncio.wait_for(process.communicate(), timeout=15)
    assert process.returncode == 0, (output.decode(), errors.decode())
