import asyncio
import hashlib
import hmac
import logging
from typing import Literal
from uuid import uuid4

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel

from perdix.gateway.config import GatewaySecrets
from perdix.gateway.events import Job
from perdix.gateway.runtime import GatewayRuntime, SubmitRequest

logger = logging.getLogger(__name__)


class TelegramIdentity(BaseModel):
    id: int


class TelegramMessage(BaseModel):
    chat: TelegramIdentity
    sender: TelegramIdentity = Field(alias="from")
    text: str = ""


class TelegramUpdate(BaseModel):
    update_id: int
    message: TelegramMessage | None = None


class TelegramEnvelope(BaseModel):
    ok: bool
    result: list[TelegramUpdate] = Field(default_factory=list)


class GitHubIssue(BaseModel):
    number: int = Field(gt=0)
    title: str
    body: str | None = None
    pull_request: dict[str, str] | None = None


class GitHubRepository(BaseModel):
    full_name: str


class GitHubSender(BaseModel):
    login: str


class GitHubComment(BaseModel):
    id: int
    body: str


class GitHubWebhook(BaseModel):
    action: str
    repository: GitHubRepository
    sender: GitHubSender
    issue: GitHubIssue
    comment: GitHubComment


class GitHubImport(BaseModel):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)
    project: str = Field(min_length=1, max_length=100)
    issue_number: int = Field(gt=0)
    instruction: str = Field(default="이 이슈를 분석하고 수정한 뒤 검증해 주세요.", max_length=8000)


class ChannelStatus(BaseModel):
    telegram: Literal["disabled", "connecting", "connected", "error"] = "disabled"
    github: Literal["disabled", "configured"] = "disabled"


class GatewayChannels:
    def __init__(
        self,
        runtime: GatewayRuntime,
        secrets: GatewaySecrets,
        http: httpx2.AsyncClient,
    ) -> None:
        self.runtime = runtime
        self.secrets = secrets
        self.http = http
        self.status = ChannelStatus(
            telegram="connecting" if secrets.telegram else "disabled",
            github="configured" if runtime.config.github else "disabled",
        )

    def project_for_repository(self, repository: str) -> str | None:
        return next(
            (
                name
                for name, p in self.runtime.config.projects.items()
                if p.github_repository == repository
            ),
            None,
        )

    async def import_issue(self, request: GitHubImport, source_id: str | None = None) -> Job:
        project = self.runtime.config.projects.get(request.project)
        if not project or not project.github_repository:
            raise ValueError("프로젝트에 GitHub 저장소를 등록하세요")
        repository = project.github_repository
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
        if self.secrets.github:
            headers["Authorization"] = f"Bearer {self.secrets.github.get_secret_value()}"
        response = await self.http.get(
            f"https://api.github.com/repos/{repository}/issues/{request.issue_number}",
            headers=headers,
        )
        response.raise_for_status()
        issue = GitHubIssue.model_validate(response.json())
        if issue.pull_request is not None:
            raise ValueError("1차 연동은 Pull Request가 아닌 이슈만 받습니다")
        prompt = (
            f"GitHub 이슈 {repository}#{issue.number}\n제목: {issue.title}\n"
            f"본문:\n{issue.body or '(본문 없음)'}\n\n요청: {request.instruction}"
        )
        return await self.runtime.submit(
            SubmitRequest(project=request.project, prompt=prompt),
            "github",
            source_id or f"github:{repository}:{issue.number}:import-{uuid4()}",
        )

    def verify_signature(self, body: bytes, signature: str) -> bool:
        if not self.secrets.webhook:
            return False
        expected = (
            "sha256="
            + hmac.new(
                self.secrets.webhook.get_secret_value().encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
        )
        return hmac.compare_digest(signature.encode(), expected.encode())

    async def webhook(self, event: GitHubWebhook) -> Job | None:
        github = self.runtime.config.github
        if (
            not github
            or event.action != "created"
            or event.sender.login not in github.allowed_senders
        ):
            return None
        if event.issue.pull_request is not None:
            return None
        command, _, instruction = event.comment.body.strip().partition(" ")
        if command != "/perdix":
            return None
        project = self.project_for_repository(event.repository.full_name)
        if not project:
            return None
        source_id = f"github:{event.repository.full_name}:{event.issue.number}:{event.comment.id}"
        existing = await self.runtime.store.source_job(source_id)
        if existing:
            return await self.runtime.store.get(existing)
        return await self.import_issue(
            GitHubImport(
                project=project,
                issue_number=event.issue.number,
                instruction=instruction or "이 이슈를 분석하고 수정한 뒤 검증해 주세요.",
            ),
            source_id,
        )

    async def send_telegram(self, chat_id: int, text: str) -> None:
        if not self.secrets.telegram:
            return
        token = self.secrets.telegram.get_secret_value()
        for start in range(0, len(text), 4000):
            response = await self.http.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[start : start + 4000]},
            )
            response.raise_for_status()
            if response.json().get("ok") is not True:
                raise ValueError("Telegram 알림이 거절되었습니다")

    async def handle_telegram(self, update: TelegramUpdate) -> None:
        config = self.runtime.config.telegram
        message = update.message
        if not config or not message:
            return
        if (
            message.sender.id not in config.allowed_user_ids
            or message.chat.id not in config.allowed_chat_ids
        ):
            return
        chat_id = message.chat.id
        command, _, arguments = message.text.strip().partition(" ")
        command = command.split("@")[0]
        match command:
            case "/run":
                project, _, prompt = arguments.partition(" ")
                source_id = f"telegram:{chat_id}:{message.sender.id}:{update.update_id}"
                job = await self.runtime.submit(
                    SubmitRequest(project=project, prompt=prompt), "telegram", source_id
                )
                await self.send_telegram(
                    chat_id,
                    f"작업을 접수했습니다.\n작업 ID: {job.id}\n"
                    "승인이 필요하면 웹 대시보드에서 처리하세요.",
                )
            case "/projects":
                await self.send_telegram(
                    chat_id, "등록 프로젝트:\n" + "\n".join(self.runtime.config.projects)
                )
            case "/status" | "/cancel":
                owned_job = await self.runtime.store.get(arguments.strip())
                prefix = f"telegram:{chat_id}:{message.sender.id}:"
                if (
                    not owned_job
                    or not owned_job.source_id
                    or not owned_job.source_id.startswith(prefix)
                ):
                    await self.send_telegram(
                        chat_id, "본인이 Telegram으로 요청한 작업 ID를 입력하세요."
                    )
                    return
                if command == "/cancel":
                    await self.runtime.cancel(owned_job.id)
                    await self.send_telegram(chat_id, "취소 요청을 처리했습니다.")
                else:
                    await self.send_telegram(
                        chat_id,
                        f"작업 상태: {owned_job.status}\n{owned_job.result[:3500]}",
                    )
            case _:
                await self.send_telegram(
                    chat_id,
                    "사용법:\n/projects\n/run 프로젝트 작업 내용\n/status 작업ID\n/cancel 작업ID",
                )

    async def poll_telegram(self) -> None:
        if not self.secrets.telegram:
            return
        token = self.secrets.telegram.get_secret_value()
        assert self.runtime.store.db is not None
        async with self.runtime.store.db.execute(
            "SELECT data FROM gateway_events WHERE kind='telegramCursor' ORDER BY seq DESC LIMIT 1",
        ) as cursor:
            row = await cursor.fetchone()
        offset = int(row[0]) if row else 0
        while True:
            try:
                response = await self.http.get(
                    f"https://api.telegram.org/bot{token}/getUpdates",
                    params={"offset": offset, "timeout": 25},
                    timeout=35,
                )
                response.raise_for_status()
                envelope = TelegramEnvelope.model_validate(response.json())
                if not envelope.ok:
                    raise ValueError("Telegram polling이 거절되었습니다")
                self.status.telegram = "connected"
                for update in envelope.result:
                    try:
                        await self.handle_telegram(update)
                    except (ValueError, ValidationError):
                        if update.message:
                            await self.send_telegram(
                                update.message.chat.id,
                                "요청을 처리하지 못했습니다. 명령과 프로젝트 이름을 확인하세요.",
                            )
                    offset = update.update_id + 1
                    await self.runtime.store.append("_telegram", "telegramCursor", offset)
            except (httpx2.HTTPError, ValueError, ValidationError):
                self.status.telegram = "error"
                logger.warning("Telegram 연결 실패; 5초 후 재시도")
                await asyncio.sleep(5)

    async def finished(self, job: Job) -> None:
        try:
            if job.source == "telegram" and job.source_id:
                chat_id = int(job.source_id.split(":")[1])
                await self.send_telegram(
                    chat_id, f"작업 {job.id}\n상태: {job.status}\n{job.result[:12000]}"
                )
            github = self.runtime.config.github
            if (
                job.source == "github"
                and job.source_id
                and github
                and github.post_results
                and self.secrets.github
            ):
                _, repository, issue, _ = job.source_id.split(":", 3)
                response = await self.http.post(
                    f"https://api.github.com/repos/{repository}/issues/{issue}/comments",
                    headers={
                        "Authorization": f"Bearer {self.secrets.github.get_secret_value()}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2026-03-10",
                    },
                    json={"body": f"Perdix 작업 `{job.id}`: {job.status}\n\n{job.result[:12000]}"},
                )
                response.raise_for_status()
        except (httpx2.HTTPError, ValueError):
            await self.runtime.store.append(
                job.id, "notificationFailed", "결과 알림 전송에 실패했습니다."
            )
