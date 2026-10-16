from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from test_graph import ask, sink
from test_worktrees import repository

from perdix.harness.budget import Budget
from perdix.harness.hooks import HookBus
from perdix.harness.modes import BUILD
from perdix.harness.turn import TurnRunner
from perdix.llm.fake import FakeProvider
from perdix.permissions.policy import PermissionPolicy
from perdix.session.store import EventStore
from perdix.tools.fs import WriteTool
from perdix.tools.registry import ToolRegistry
from perdix.workflows.commands import WorkflowCommand, WorkflowCommandError, parse_workflow_command
from perdix.workflows.models import WorkflowPlan, WorkflowTask
from perdix.workflows.service import WorkflowAccessError, WorkflowContext, WorkflowService
from perdix.workflows.store import WorkflowStore


def plan() -> WorkflowPlan:
    return WorkflowPlan(
        objective="write marker",
        tasks=(
            WorkflowTask(
                task_id="implementation",
                title="write marker",
                role="implement",
                model="fake-model",
                paths=("marker.txt",),
                acceptance=("marker exists",),
            ),
        ),
        max_revision_rounds=0,
    )


async def setup(
    tmp_path: Path, provider: FakeProvider
) -> tuple[WorkflowService, WorkflowContext, EventStore]:
    root, worktrees, cancel = await repository(tmp_path)
    (root / ".perdix").mkdir()
    config = '{"verify":{"commands":[{"name":"marker","run":"test -f marker.txt"}]}}'
    (root / ".perdix/config.json").write_text(config)
    await worktrees.git(root, "add", ".", cancel=cancel)
    await worktrees.git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-m",
        "config",
        cancel=cancel,
    )
    events = await EventStore.open(tmp_path / "events.sqlite")
    turn = TurnRunner(
        store=events,
        provider=provider,
        registry=ToolRegistry([WriteTool()]),
        policy=PermissionPolicy(),
        sandbox=worktrees.backend,
        budget=Budget(),
        hooks=HookBus(),
        mode=BUILD,
        cwd=root,
        model="fake-model",
        system_prompt_builder=lambda cwd, mode: "",
    )
    return (
        WorkflowService(WorkflowStore(tmp_path / "workflow.sqlite"), tmp_path / "storage"),
        WorkflowContext("parent", root, turn, cancel, sink, ask, True, config),
        events,
    )


@pytest.mark.asyncio
async def test_plan_outside_git_reports_actionable_error(tmp_path: Path) -> None:
    provider = FakeProvider([])
    service, context, events = await setup(tmp_path, provider)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        with pytest.raises(WorkflowCommandError, match="Git 저장소.*일반 프롬프트"):
            await service.plan("구조를 분석해줘", replace(context, project_root=outside))
        assert provider.requests == []
    finally:
        await events.close()


@pytest.mark.asyncio
async def test_natural_plan_uses_provider_without_creating_worktree(tmp_path: Path) -> None:
    # Given: 모델 계획 응답과 수정된 원본 파일.
    provider = FakeProvider([{"text": plan().model_dump_json()}])
    service, context, events = await setup(tmp_path, provider)
    (context.project_root / "file.txt").write_text("dirty")
    try:
        # When: 자연어 요청을 계획한다.
        snapshot = await service.plan("write marker", context)
        # Then: 검증된 계획만 저장되고 원본 수정이나 worktree 생성이 없다.
        assert snapshot.run.status == "pending"
        assert snapshot.attempts == ()
        assert len(provider.requests) == 1
        assert service._worktrees(context).preserved() == ()
        assert (context.project_root / "file.txt").read_text() == "dirty"
    finally:
        await events.close()


@pytest.mark.asyncio
async def test_start_verify_review_then_accept_updates_original(tmp_path: Path) -> None:
    # Given: 실제 쓰기 도구와 검증 명령, 구조화 리뷰.
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
    service, context, events = await setup(tmp_path, provider)
    planned = await service.plan(plan().model_dump_json(), context)
    try:
        # When: 시작 후 별도 명시적 수락 명령을 실행한다.
        ready = await service.execute(
            WorkflowCommand(action="start", run_id=planned.run.run_id), context
        )
        assert ready.run.status == "completed"
        assert ready.verification is not None and ready.verification.status == "passed"
        assert not (context.project_root / "marker.txt").exists()
        accepted = await service.execute(
            WorkflowCommand(action="accept", run_id=planned.run.run_id), context
        )
        # Then: 실제 원본 HEAD가 검증 후보와 같아지고 파일이 나타난다.
        assert accepted.run.acceptance_status == "accepted"
        assert (context.project_root / "marker.txt").read_text() == "verified"
        head = await service._worktrees(context).git(
            context.project_root, "rev-parse", "HEAD", cancel=context.cancel
        )
        assert head == ready.run.candidate_commit
    finally:
        await events.close()


@pytest.mark.asyncio
async def test_foreign_session_and_config_drift_cannot_start(tmp_path: Path) -> None:
    # Given: 저장된 명시적 계획.
    service, context, events = await setup(tmp_path, FakeProvider([]))
    snapshot = await service.plan(plan().model_dump_json(), context)
    command = WorkflowCommand(action="start", run_id=snapshot.run.run_id)
    try:
        # When: 다른 세션 또는 다른 고정 설정으로 시작을 요청한다.
        with pytest.raises(WorkflowAccessError):
            await service.execute(command, replace(context, session_id="foreign"))
        with pytest.raises(WorkflowCommandError):
            await service.execute(command, replace(context, trusted_config_json="{}"))
        # Then: 실행 attempt가 생성되지 않는다.
        assert (await service.status(snapshot.run.run_id, context.project_root)).attempts == ()
    finally:
        await events.close()


@pytest.mark.parametrize("text", ["/workflow", "/workflow start a b", "/workflow retry a"])
def test_malformed_command_is_rejected(text: str) -> None:
    with pytest.raises(WorkflowCommandError):
        parse_workflow_command(text)


@pytest.mark.asyncio
async def test_repair_accept_selects_verified_candidate_and_repeated_accept_is_safe(
    tmp_path: Path,
) -> None:
    # Given: 첫 구현을 리뷰가 거절하고 두 번째 구현을 승인한다.
    provider = FakeProvider(
        [
            {
                "tool_uses": [
                    {
                        "id": "one",
                        "name": "write",
                        "input": {"path": "marker.txt", "content": "first"},
                    }
                ]
            },
            {"text": "done"},
            {"text": '{"repair_tasks":["implementation"]}'},
            {
                "tool_uses": [
                    {
                        "id": "two",
                        "name": "write",
                        "input": {"path": "marker.txt", "content": "second"},
                    }
                ]
            },
            {"text": "done"},
            {"text": '{"repair_tasks":[]}'},
        ]
    )
    service, context, events = await setup(tmp_path, provider)
    planned = await service.plan(
        plan().model_copy(update={"max_revision_rounds": 1}).model_dump_json(), context
    )
    try:
        # When: 수정 루프 완료 후 수락을 두 번 요청한다.
        ready = await service.execute(
            WorkflowCommand(action="start", run_id=planned.run.run_id), context
        )
        assert len(ready.attempts) == 2
        assert ready.run.status == "completed"
        command = WorkflowCommand(action="accept", run_id=planned.run.run_id)
        accepted = await service.execute(command, context)
        repeated = await service.execute(command, context)
        # Then: 첫 후보가 아닌 검증된 두 번째 후보를 한 번 반영한다.
        assert repeated.run.candidate_commit == accepted.run.candidate_commit
        assert repeated.acceptance is not None and accepted.acceptance is not None
        assert repeated.acceptance.receipt_id == accepted.acceptance.receipt_id
        assert (context.project_root / "marker.txt").read_text() == "second"
    finally:
        await events.close()


@pytest.mark.asyncio
async def test_dirty_source_plans_but_start_blocks_without_provider_call(tmp_path: Path) -> None:
    # Given: 계획 뒤 원본에 미커밋 변경이 생겼다.
    provider = FakeProvider([])
    service, context, events = await setup(tmp_path, provider)
    planned = await service.plan(plan().model_dump_json(), context)
    (context.project_root / "file.txt").write_text("user change")
    try:
        # When: 사용자가 시작을 요청한다.
        result = await service.execute(
            WorkflowCommand(action="start", run_id=planned.run.run_id), context
        )
        # Then: 실패가 저장되고 공급자 실행과 원본 덮어쓰기가 없다.
        assert result.run.status == "blocked"
        assert result.attempts[0].status == "failed"
        assert provider.requests == []
        assert (context.project_root / "file.txt").read_text() == "user change"
    finally:
        await events.close()


@pytest.mark.asyncio
async def test_foreign_project_status_is_denied(tmp_path: Path) -> None:
    # Given: 프로젝트에 결합된 영속 계획.
    service, context, events = await setup(tmp_path, FakeProvider([]))
    planned = await service.plan(plan().model_dump_json(), context)
    try:
        # When: 다른 프로젝트로 상태를 조회한다.
        with pytest.raises(WorkflowAccessError):
            await service.status(planned.run.run_id, tmp_path / "foreign")
        # Then: 실제 소유 프로젝트에서는 조회할 수 있다.
        assert (await service.status(planned.run.run_id, context.project_root)).run == planned.run
    finally:
        await events.close()


@pytest.mark.asyncio
async def test_invalid_review_blocks_verified_candidate_acceptance(tmp_path: Path) -> None:
    # Given: 구현과 명령 검증은 통과하지만 리뷰 JSON은 잘못되었다.
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
            {"text": "not json"},
        ]
    )
    service, context, events = await setup(tmp_path, provider)
    planned = await service.plan(plan().model_dump_json(), context)
    try:
        # When: 시작하여 리뷰 응답을 검증한다.
        result = await service.execute(
            WorkflowCommand(action="start", run_id=planned.run.run_id), context
        )
        # Then: 실행은 blocked이며 원본 반영은 허용되지 않는다.
        assert result.run.status == "blocked"
        assert result.verification is not None and result.verification.status == "passed"
        assert result.phases[-1].status == "failed"
        assert result.run.failure_reason is not None
        assert not (context.project_root / "marker.txt").exists()
        assert list((service.storage_root / "failures" / planned.run.run_id).glob("*.txt"))
        with pytest.raises(WorkflowCommandError):
            await service.execute(
                WorkflowCommand(action="accept", run_id=planned.run.run_id), context
            )
    finally:
        await events.close()
