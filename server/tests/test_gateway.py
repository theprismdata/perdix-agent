from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import shlex
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from perdix.config.schema import ServerConfig
from perdix.gateway.app import create_app
from perdix.gateway.channels import GatewayChannels, TelegramUpdate
from perdix.gateway.config import (
    GatewayConfig,
    GatewaySecrets,
    GitHubConfig,
    ProjectConfig,
    TelegramConfig,
)
from perdix.gateway.events import GatewayStore, Job
from perdix.gateway.runtime import GatewayRuntime, SubmitRequest

TOKEN = "test-gateway-token-with-32-characters"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def configuration(tmp: Path, script: list[dict[str, Any]] | None = None) -> GatewayConfig:
    project = tmp / "project"
    project.mkdir(exist_ok=True)
    script_path = tmp / "fake.json"
    script_path.write_text(
        json.dumps(script or [{"text": "검증한 결과입니다.", "stop": "end_turn"}])
    )
    credentials = tmp / "credentials.json"
    credentials.write_text("{}")
    return GatewayConfig(
        db_path=tmp / "gateway.db",
        projects={"sample": ProjectConfig(cwd=project, github_repository="example/repository")},
        server=ServerConfig(
            provider="fake",
            fake_script=script_path,
            db_path=str(tmp / "agent.db"),
            credentials_path=credentials,
        ),
    )


@asynccontextmanager
async def gateway(
    config: GatewayConfig,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> AsyncIterator[tuple[httpx2.AsyncClient, GatewayRuntime, GatewayChannels]]:
    app = create_app(
        config,
        GatewaySecrets(gateway=SecretStr(TOKEN), webhook=SecretStr("webhook-test-secret")),
        transport=transport,
    )
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver", headers=AUTH
        ) as client:
            yield client, app.state.runtime, app.state.channels


async def finish(runtime: GatewayRuntime, job_id: str) -> Job:
    task = runtime.tasks.get(job_id)
    if task:
        await asyncio.wait_for(asyncio.shield(task), 30)
    job = await runtime.store.get(job_id)
    assert job is not None
    return job


async def wait_permission(runtime: GatewayRuntime, job_id: str) -> str:
    async with asyncio.timeout(30), runtime.store.changed:
        while not any(p.job_id == job_id for p in runtime.permissions.values()):
            job = await runtime.store.get(job_id)
            assert job is None or job.status in ("queued", "running", "waiting"), job
            await runtime.store.changed.wait()
    return next(key for key, value in runtime.permissions.items() if value.job_id == job_id)


async def test_authentication_when_token_missing(tmp_path: Path) -> None:
    async with gateway(configuration(tmp_path)) as (client, _, _):
        response = await client.get("/api/jobs", headers={"Authorization": "Bearer invalid"})
        assert response.status_code == 401


async def test_project_rejected_when_not_registered(tmp_path: Path) -> None:
    async with gateway(configuration(tmp_path)) as (client, runtime, _):
        response = await client.post("/api/jobs", json={"project": "../outside", "prompt": "run"})
        assert response.status_code == 400
        assert await runtime.store.jobs() == []


async def test_job_completes_when_submitted_over_http(tmp_path: Path) -> None:
    async with gateway(configuration(tmp_path)) as (client, runtime, _):
        response = await client.post("/api/jobs", json={"project": "sample", "prompt": "결과 확인"})
        assert response.status_code == 202
        job = await finish(runtime, response.json()["id"])
        assert (job.status, job.result) == ("completed", "검증한 결과입니다.")
        events = (await client.get(f"/api/jobs/{job.id}/events")).json()
        assert any(
            e["kind"] == "update" and e["data"]["sessionUpdate"] == "agent_message_chunk"
            for e in events
        )
        assert (await client.get("/api/jobs")).json()[0]["sessionId"] == job.session_id


async def test_source_deduplicated_when_delivered_concurrently(tmp_path: Path) -> None:
    async with gateway(configuration(tmp_path)) as (_, runtime, _):
        jobs = await asyncio.gather(
            *(
                runtime.submit(SubmitRequest(project="sample", prompt="run"), "web", "same-update")
                for _ in range(3)
            )
        )
        assert len({job.id for job in jobs}) == 1
        assert len(await runtime.store.jobs()) == 1
        await finish(runtime, jobs[0].id)


WRITE_SCRIPT = [
    {
        "tool_uses": [
            {
                "id": "write-1",
                "name": "write",
                "input": {"path": "approved.txt", "content": "approved"},
            }
        ],
        "stop": "tool_use",
    },
    {"text": "파일 저장 완료", "stop": "end_turn"},
]


async def test_file_written_when_permission_approved(tmp_path: Path) -> None:
    async with gateway(configuration(tmp_path, WRITE_SCRIPT)) as (client, runtime, _):
        job = (
            await client.post("/api/jobs", json={"project": "sample", "prompt": "파일 저장"})
        ).json()
        permission_id = await wait_permission(runtime, job["id"])
        assert not (tmp_path / "project" / "approved.txt").exists()
        response = await client.post(
            f"/api/jobs/{job['id']}/permissions/{permission_id}", json={"optionId": "allow_once"}
        )
        assert response.status_code == 200
        assert (await finish(runtime, job["id"])).status == "completed"
        assert (tmp_path / "project" / "approved.txt").read_text() == "approved"


async def test_cross_job_approval_rejected_when_id_mismatched(tmp_path: Path) -> None:
    async with gateway(configuration(tmp_path, WRITE_SCRIPT)) as (client, runtime, _):
        job = await runtime.submit(SubmitRequest(project="sample", prompt="write"))
        permission_id = await wait_permission(runtime, job.id)
        response = await client.post(
            f"/api/jobs/other/permissions/{permission_id}", json={"optionId": "allow_once"}
        )
        assert response.status_code == 409
        assert not (tmp_path / "project" / "approved.txt").exists()


async def test_job_cancelled_when_waiting_for_permission(tmp_path: Path) -> None:
    async with gateway(configuration(tmp_path, WRITE_SCRIPT)) as (client, runtime, _):
        job = await runtime.submit(SubmitRequest(project="sample", prompt="write"))
        await wait_permission(runtime, job.id)
        response = await client.post(f"/api/jobs/{job.id}/cancel")
        assert response.json()["status"] == "cancelled"
        assert runtime.permissions == {}
        assert not (tmp_path / "project" / "approved.txt").exists()


async def test_restart_marks_job_interrupted_when_unfinished(tmp_path: Path) -> None:
    config = configuration(tmp_path)
    store = GatewayStore(config.db_path)
    await store.open()
    job = Job(
        id="interrupted-job",
        project="sample",
        prompt="old",
        source="web",
        source_id=None,
        created_at=1,
    )
    await store.append(job.id, "submitted", job.model_dump(mode="json"))
    await store.append(job.id, "state", {"status": "running"})
    await store.close()
    async with gateway(config) as (_, runtime, _):
        recovered = await runtime.store.get(job.id)
        assert recovered is not None and recovered.status == "interrupted"
        assert [e.kind for e in await runtime.store.events(job.id)] == [
            "submitted",
            "state",
            "state",
        ]


def webhook_body(sender: str = "operator") -> bytes:
    return json.dumps(
        {
            "action": "created",
            "sender": {"login": sender},
            "repository": {"full_name": "example/repository"},
            "issue": {"number": 42, "title": "Problem"},
            "comment": {"id": 123, "body": "/perdix 원인 분석"},
        }
    ).encode()


def signed_headers(body: bytes) -> dict[str, str]:
    signature = hmac.new(b"webhook-test-secret", body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={signature}", "X-GitHub-Event": "issue_comment"}


async def test_webhook_rejected_when_signature_invalid(tmp_path: Path) -> None:
    async with gateway(configuration(tmp_path)) as (client, runtime, _):
        response = await client.post(
            "/webhooks/github",
            content=webhook_body(),
            headers={"X-Hub-Signature-256": "sha256=invalid"},
        )
        assert response.status_code == 401
        assert await runtime.store.jobs() == []


async def test_webhook_ignored_when_sender_not_allowed(tmp_path: Path) -> None:
    config = configuration(tmp_path).model_copy(
        update={"github": GitHubConfig(allowed_senders=["operator"])}
    )
    async with gateway(config) as (client, runtime, _):
        body = webhook_body("stranger")
        response = await client.post("/webhooks/github", content=body, headers=signed_headers(body))
        assert response.json() == {"accepted": False, "jobId": None}
        assert await runtime.store.jobs() == []


async def test_github_issue_job_when_signed_comment_delivered(tmp_path: Path) -> None:
    calls = []

    def github_http(request: httpx2.Request) -> httpx2.Response:
        calls.append(str(request.url))
        return httpx2.Response(
            200, json={"number": 42, "title": "Problem", "body": "Issue details"}
        )

    config = configuration(tmp_path).model_copy(
        update={"github": GitHubConfig(allowed_senders=["operator"])}
    )
    async with gateway(config, httpx2.MockTransport(github_http)) as (client, runtime, _):
        body = webhook_body()
        response = await client.post("/webhooks/github", content=body, headers=signed_headers(body))
        repeated = await client.post("/webhooks/github", content=body, headers=signed_headers(body))
        assert response.status_code == 202
        assert repeated.json()["jobId"] == response.json()["jobId"]
        job = await finish(runtime, response.json()["jobId"])
        assert job.source == "github" and "Issue details" in job.prompt
        assert calls == ["https://api.github.com/repos/example/repository/issues/42"]


async def test_github_import_when_issue_requested_from_dashboard(tmp_path: Path) -> None:
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(200, json={"number": 42, "title": "Fix"})
    )
    async with gateway(configuration(tmp_path), transport) as (client, runtime, _):
        response = await client.post(
            "/api/github/import", json={"project": "sample", "issueNumber": 42}
        )
        assert response.status_code == 202
        assert (await finish(runtime, response.json()["id"])).source == "github"


async def test_telegram_job_when_allowlisted_user_sends_run(tmp_path: Path) -> None:
    sent = []

    def telegram_http(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json={"ok": True, "result": {}})

    config = configuration(tmp_path).model_copy(
        update={
            "telegram": TelegramConfig(allowed_user_ids=[7], allowed_chat_ids=[8]),
        }
    )
    runtime = GatewayRuntime(config, GatewayStore(config.db_path))
    await runtime.start()
    try:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(telegram_http)) as http:
            channels = GatewayChannels(
                runtime,
                GatewaySecrets(gateway=SecretStr(TOKEN), telegram=SecretStr("test-bot-token")),
                http,
            )
            runtime.on_finished = channels.finished
            await channels.handle_telegram(
                TelegramUpdate.model_validate(
                    {
                        "update_id": 9,
                        "message": {
                            "chat": {"id": 8},
                            "from": {"id": 7},
                            "text": "/run sample 분석",
                        },
                    }
                )
            )
            job = (await runtime.store.jobs())[0]
            assert (await finish(runtime, job.id)).status == "completed"
            assert len(sent) == 2 and all(item["chat_id"] == 8 for item in sent)
            assert "검증한 결과입니다." in sent[1]["text"]
    finally:
        await runtime.close()


@pytest.mark.parametrize("user,chat", [(99, 8), (7, 99)])
async def test_telegram_ignored_when_identity_not_allowed(
    tmp_path: Path, user: int, chat: int
) -> None:
    config = configuration(tmp_path).model_copy(
        update={
            "telegram": TelegramConfig(allowed_user_ids=[7], allowed_chat_ids=[8]),
        }
    )
    async with gateway(config) as (_, runtime, channels):
        await channels.handle_telegram(
            TelegramUpdate.model_validate(
                {
                    "update_id": 1,
                    "message": {
                        "chat": {"id": chat},
                        "from": {"id": user},
                        "text": "/run sample run",
                    },
                }
            )
        )
        assert await runtime.store.jobs() == []


async def test_secret_removed_when_custom_environment_name_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHANNEL_VALUE", "private-channel-secret")
    code = (
        "import os; from pathlib import Path; "
        "Path('env.txt').write_text(os.environ.get('CHANNEL_VALUE', 'absent'))"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
    script = [
        {
            "tool_uses": [{"id": "shell", "name": "bash", "input": {"command": command}}],
            "stop": "tool_use",
        },
        {"text": "done", "stop": "end_turn"},
    ]
    config = configuration(tmp_path, script).model_copy(update={"token_env": "CHANNEL_VALUE"})
    async with gateway(config) as (client, runtime, _):
        job = await runtime.submit(SubmitRequest(project="sample", prompt="check env"))
        permission_id = await wait_permission(runtime, job.id)
        await client.post(
            f"/api/jobs/{job.id}/permissions/{permission_id}", json={"optionId": "allow_once"}
        )
        await finish(runtime, job.id)
        assert (tmp_path / "project" / "env.txt").read_text() == "absent"


async def test_cancelled_when_task_has_not_started(tmp_path: Path) -> None:
    async with gateway(configuration(tmp_path)) as (_, runtime, _):
        async with runtime.limit:
            job = await runtime.submit(SubmitRequest(project="sample", prompt="cancel"))
            await runtime.cancel(job.id)
        result = await runtime.store.get(job.id)
        assert result is not None and result.status == "cancelled"


async def test_signed_webhook_rejected_when_signature_has_unicode(tmp_path: Path) -> None:
    async with gateway(configuration(tmp_path)) as (_, _, channels):
        assert not channels.verify_signature(webhook_body(), "sha256=한글")
