"""실제 SQLite 기록을 인증 HTTP 경로로 조회하는 Gateway 회귀 검증."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest
import pytest_asyncio
from pydantic import SecretStr

from perdix.config.schema import ServerConfig
from perdix.gateway.app import create_app
from perdix.gateway.config import GatewayConfig, GatewaySecrets, ProjectConfig
from perdix.gateway.events import Job
from perdix.gateway.runtime import GatewayRuntime
from perdix.teams.models import TeamCommand
from perdix.teams.store import TeamStore
from perdix.workflows.models import WorkflowPlan, WorkflowRun, WorkflowTask
from perdix.workflows.store import WorkflowStore

TOKEN = "test-gateway-token-with-enough-characters"


@pytest_asyncio.fixture
async def gateway(tmp_path: Path) -> AsyncIterator[tuple[httpx2.AsyncClient, GatewayRuntime]]:
    other = tmp_path / "other"
    other.mkdir()
    config = GatewayConfig(
        db_path=tmp_path / "gateway.db",
        projects={"main": ProjectConfig(cwd=tmp_path), "other": ProjectConfig(cwd=other)},
        server=ServerConfig(db_path=str(tmp_path / "sessions.db"), team_db=tmp_path / "teams.db"),
    )
    app = create_app(config, GatewaySecrets(gateway=SecretStr(TOKEN)))
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver",
            headers={"Authorization": "Bearer " + TOKEN},
        ) as client:
            yield client, app.state.runtime


async def seed(runtime: GatewayRuntime, *, session: str = "session") -> WorkflowStore:
    store = WorkflowStore(Path(runtime.config.server.db_path).with_name("workflows.sqlite3"))
    plan = WorkflowPlan(objective="test", tasks=(WorkflowTask(
        task_id="task", title="test", role="implement", model="fake", paths=("src",),
        acceptance=("pass",),
    ),))
    await store.create_run(WorkflowRun(
        run_id="run", session_id=session, project_root=str(runtime.config.projects["main"].cwd),
        base_commit="base", target_branch="main", plan=plan,
    ))
    job = Job(id="job", project="main", prompt="test", source="web", source_id=None, created_at=1)
    await runtime.store.append(job.id, "submitted", job.model_dump(mode="json"))
    await runtime.store.bind_workflow("job", "session", "run")
    return store


@pytest.mark.asyncio
async def test_binding_survives_restart_and_partial_states(gateway) -> None:
    # Given: 연결 후 상태 이벤트가 세션 ID 없이 기록되었다.
    client, runtime = gateway
    await seed(runtime)
    await runtime.store.append("job", "state", {"status": "running", "result": "kept"})
    await runtime.store.close()
    # When: Gateway가 다시 열리고 interrupted 상태를 기록한다.
    await runtime.start()
    response = await client.get("/api/jobs/job/workflow")
    # Then: 동일 run/session 연결과 이전 result가 보존된다.
    assert response.status_code == 200
    assert response.json()["run"]["runId"] == "run"
    job = await runtime.store.get("job")
    assert job is not None
    assert (job.session_id, job.workflow_run_id, job.status) == ("session", "run", "interrupted")
    assert (await runtime.store.jobs())[0] == job


@pytest.mark.asyncio
async def test_reconnect_returns_gap_beyond_thousand(gateway) -> None:
    # Given: 클라이언트의 cursor 이후에 1001개 이벤트가 저장되었다.
    client, runtime = gateway
    store = await seed(runtime)
    run = (await store.snapshot("run")).run
    for _ in range(1001):
        await store.save_run(run)
    # When: 마지막 cursor 다음 이벤트를 조회한다.
    response = await client.get("/api/jobs/job/workflow/events?after=1")
    # Then: 마지막 seq까지 단 하나의 gap/중복 없이 반환된다.
    assert response.status_code == 200
    page = response.json()
    assert [event["seq"] for event in page["events"]] == list(range(2, 1003))
    assert page["seq"] == 1002
    empty = await client.get("/api/jobs/job/workflow/events?after=1002")
    assert empty.json()["events"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/api/jobs/job/workflow", "/api/jobs/job/workflow/events", "/api/teams?project=main",
    "/api/teams/id?project=main",
])
async def test_read_routes_require_bearer(gateway, path: str) -> None:
    # Given: 인증 헤더가 없는 요청이다.
    client, _ = gateway
    # When: 신규 읽기 경로에 접근한다.
    response = await client.get(path, headers={"Authorization": ""})
    # Then: 데이터를 반환하지 않는다.
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["session", "project", "missing"])
async def test_invalid_binding_is_not_visible(gateway, binding: str) -> None:
    # Given: 실제 run과 다른 세션/프로젝트이거나 연결이 없는 작업이다.
    client, runtime = gateway
    await seed(runtime, session="foreign" if binding == "session" else "session")
    if binding == "project":
        await runtime.store.append("job", "state", {"project": "other"})
    if binding == "missing":
        await runtime.store.append("job", "workflow_binding", {"workflow_run_id": None})
    # When: snapshot을 요청한다.
    response = await client.get("/api/jobs/job/workflow")
    # Then: 타 세션/프로젝트의 기록은 노출되지 않는다.
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_team_query_is_project_scoped_and_read_only(gateway) -> None:
    # Given: 같은 이름의 팀이 서로 다른 프로젝트에 존재한다.
    client, runtime = gateway
    config = runtime.config
    leader = TeamStore(config.server.team_db, config.projects["main"].cwd,
                       "team", "lead", "lead-session", "leader")
    other = TeamStore(config.server.team_db, config.projects["other"].cwd,
                      "team", "other", "other-session", "leader")
    await leader.join()
    await other.join()
    await leader.command(TeamCommand(action="assign", task_id="t", recipient="lead",
                                     body="implement", paths=["src"]))
    before = config.server.team_db.read_bytes()
    # When: 허용된 프로젝트와 교차 프로젝트 조회를 수행한다.
    listing = await client.get("/api/teams?project=main")
    detail = await client.get(f"/api/teams/{leader.team}?project=main")
    denied = await client.get(f"/api/teams/{leader.team}?project=other")
    unknown = await client.get("/api/teams?project=/tmp/arbitrary")
    # Then: 해당 팀만 나오며 DB 내용은 변경되지 않는다.
    assert [team["id"] for team in listing.json()["teams"]] == [leader.team]
    assert detail.json()["tasks"][0]["paths"] == ["src"]
    assert detail.json()["members"][0]["name"] == "lead"
    assert denied.status_code == unknown.status_code == 404
    assert config.server.team_db.read_bytes() == before


@pytest.mark.asyncio
async def test_missing_team_database_is_not_created(gateway) -> None:
    # Given: 팀 저장소가 존재하지 않는다.
    client, runtime = gateway
    # When: 팀 목록을 조회한다.
    response = await client.get("/api/teams?project=main")
    # Then: 빈 목록만 반환하고 DB는 생성하지 않는다.
    assert response.json() == {"teams": []}
    assert not runtime.config.server.team_db.exists()


@pytest.mark.asyncio
async def test_control_requires_authentication(gateway) -> None:
    # Given: 인증 없는 제어 요청이다.
    client, _ = gateway
    # When: start를 요청한다.
    response = await client.post("/api/jobs/job/workflow/control", json={"action": "start"},
                                 headers={"Authorization": ""})
    # Then: 제어 실행 전 거부한다.
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_busy_control_rejects_without_queueing(gateway) -> None:
    # Given: 해당 프로젝트의 실행 잠금을 다른 작업이 보유한다.
    import asyncio

    client, runtime = gateway
    await seed(runtime)
    # When: 활성 프로젝트에 제어를 요청한다.
    async with runtime.project_locks["main"], asyncio.timeout(1):
        response = await client.post("/api/jobs/job/workflow/control", json={"action": "start"})
    # Then: 잠금 대기 없이 409이며 작업을 추가하지 않는다.
    assert response.status_code == 409
    assert not runtime.tasks
    assert len(await runtime.store.jobs()) == 1


@pytest.mark.asyncio
async def test_repeated_metadata_binding_is_idempotent(gateway) -> None:
    # Given: 동일 run/session이 이미 연결되어 있다.
    _, runtime = gateway
    await seed(runtime)
    # When: 후속 metadata가 동일 연결을 전달한다.
    await runtime.store.bind_workflow("job", "session", "run")
    # Then: 연결 이벤트가 하나만 존재한다.
    events = await runtime.store.events("job")
    assert len([event for event in events if event.kind == "workflow_binding"]) == 1


@pytest.mark.asyncio
async def test_snapshot_projects_attempt_and_verification_receipt(gateway) -> None:
    # Given: 실제 저장소에 attempt와 실패 검증 영수증이 기록되었다.
    from perdix.workflows.models import TaskAttempt, VerificationReceipt
    from perdix.workflows.projection import VerificationRecorded, WorkflowEvent

    client, runtime = gateway
    store = await seed(runtime)
    await store.save_attempt(TaskAttempt(
        run_id="run", task_id="task", attempt_id="attempt", owner_epoch=0,
        status="failed", failure_reason="exit 1",
    ))
    await store.append_event(WorkflowEvent(
        run_id="run", idempotency_key="receipt", payload=VerificationRecorded(
            receipt=VerificationReceipt(receipt_id="receipt", run_id="run", candidate_commit="c",
                candidate_tree="tree", config_digest="digest", status="failed",
            )
        ),
    ))
    # When: 인증된 snapshot을 조회한다.
    response = await client.get("/api/jobs/job/workflow")
    # Then: 저장된 실패 원인·검증 상태·연속 seq가 camelCase로 나온다.
    payload = response.json()
    assert payload["attempts"][0]["failureReason"] == "exit 1"
    assert payload["verification"]["receiptId"] == "receipt"
    assert payload["run"]["verificationStatus"] == "failed"
    assert payload["seq"] == 3
