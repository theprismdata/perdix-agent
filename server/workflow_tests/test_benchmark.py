"""동일 fake 과제의 실제 단일 하네스와 팀 실행을 비교한다."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Final, Literal, assert_never

import pytest
from test_service import setup

from perdix.context.messages import TextBlock
from perdix.harness.events import (
    HarnessEvent,
    PermissionOutcome,
    PermissionRequest,
    ToolFinished,
    ToolRunning,
    ToolStarted,
)
from perdix.llm.base import ModelEvent, ModelRequest, Usage
from perdix.llm.fake import FakeProvider
from perdix.tools.fs import WriteTool
from perdix.tools.registry import ToolRegistry
from perdix.tools.shell import BashTool
from perdix.workflows.commands import WorkflowCommand
from perdix.workflows.models import WorkflowPlan, WorkflowTask

VERIFY: Final = 'test "$(cat marker.txt)" = verified && test "$(cat summary.txt)" = complete'
OBJECTIVE: Final = "Create marker.txt containing verified and summary.txt containing complete."


class ObservedFake(FakeProvider):
    """실제 공급자 이벤트를 기록하는 mutable 테스트 관측기."""

    emitted: list[ModelEvent]

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelEvent]:
        async for event in super().stream(req):
            self.emitted.append(event)
            yield event


@pytest.mark.asyncio
async def test_same_fake_task_completes_in_single_and_team(tmp_path: Path) -> None:
    # Given: 동일한 파일 내용과 검증 명령을 사용하는 별도 실제 Git 저장소.
    writes = [
        {"id": "marker", "name": "write", "input": {"path": "marker.txt", "content": "verified"}},
        {"id": "summary", "name": "write", "input": {"path": "summary.txt", "content": "complete"}},
    ]
    plan = WorkflowPlan(
        objective=OBJECTIVE,
        tasks=tuple(
            WorkflowTask(
                task_id=name, title=name, role="implement", model=name,
                paths=(f"{name}.txt",), acceptance=(f"{name}.txt has expected content",),
            ) for name in ("marker", "summary")
        ),
        max_revision_rounds=0,
    )
    reports = []
    modes: tuple[Literal["single", "team"], ...] = ("single", "team")
    for mode in modes:
        fixture = tmp_path / mode
        fixture.mkdir()
        single = ObservedFake([
            {"tool_uses": writes},
            {"tool_uses": [{"id": "verify", "name": "bash", "input": {"command": VERIFY}}]},
            {"text": "completed"},
        ])
        reviewer = ObservedFake([{"text": '{"repair_tasks":[]}'}])
        providers = {
            "fake-model": reviewer,
            **{name: ObservedFake([{"tool_uses": [write]}, {"text": "completed"}])
               for name, write in zip(("marker", "summary"), writes, strict=True)},
        }
        all_providers = [single, *providers.values()]
        for provider in all_providers:
            provider.emitted = []
        service, context, store = await setup(fixture, single)
        config = json.dumps({"verify": {"commands": [{"name": "same-task", "run": VERIFY}]}})
        (context.project_root / ".perdix/config.json").write_text(config, encoding="utf-8")
        worktrees = service._worktrees(context)
        await worktrees.git(context.project_root, "add", ".", cancel=context.cancel)
        await worktrees.git(
            context.project_root, "-c", "user.name=Test", "-c", "user.email=test@localhost",
            "commit", "-m", "benchmark verification", cancel=context.cancel,
        )
        base = await worktrees.git(context.project_root, "rev-parse", "HEAD", cancel=context.cancel)
        observed: list[HarnessEvent] = []
        permissions: list[PermissionRequest] = []

        async def sink(
            event: HarnessEvent, captured: list[HarnessEvent] = observed,
        ) -> None:
            captured.append(event)

        async def ask(
            request: PermissionRequest, captured: list[PermissionRequest] = permissions,
        ) -> PermissionOutcome:
            captured.append(request)
            return PermissionOutcome(outcome="allow_once")

        context = replace(context, sink=sink, ask_permission=ask, trusted_config_json=config)
        started = perf_counter()
        attempt_count = 1
        reruns = 0
        control_actions = ["prompt"]
        verification_commands = 1
        try:
            # When: 실제 단일 TurnRunner 또는 plan/start/accept 팀 서비스를 실행한다.
            match mode:
                case "single":
                    context.turn.registry = ToolRegistry([WriteTool(), BashTool()])
                    session = await store.create_session(
                        str(context.project_root), "build", "fake", "fake-model",
                    )
                    result = await context.turn.run(
                        session.id, [TextBlock(text=OBJECTIVE)], cancel=context.cancel,
                        sink=sink, ask_permission=ask,
                    )
                    assert result.stop_reason == "end_turn"
                    verification = next(
                        event for event in observed
                        if isinstance(event, ToolFinished) and event.tool_use_id == "verify"
                    )
                    assert not verification.output.is_error
                    steps = context.turn.budget.steps_used
                    candidate = None
                case "team":
                    context.turn.compactor_factory = providers.__getitem__
                    planned = await service.plan(plan.model_dump_json(), context)
                    ready = await service.execute(
                        WorkflowCommand(action="start", run_id=planned.run.run_id), context,
                    )
                    assert ready.run.status == "completed"
                    assert ready.verification is not None and ready.verification.status == "passed"
                    assert await worktrees.git(
                        context.project_root, "rev-parse", "HEAD", cancel=context.cancel,
                    ) == base
                    assert not (context.project_root / "marker.txt").exists()
                    accepted = await service.execute(
                        WorkflowCommand(action="accept", run_id=planned.run.run_id), context,
                    )
                    assert accepted.run.acceptance_status == "accepted"
                    candidate = accepted.run.candidate_commit
                    assert await worktrees.git(
                        context.project_root, "rev-parse", "HEAD", cancel=context.cancel,
                    ) == candidate
                    attempt_count = len(ready.attempts)
                    reruns = attempt_count - len({attempt.task_id for attempt in ready.attempts})
                    steps = ready.steps_used
                    verification_commands = len(ready.verification.commands)
                    control_actions = ["plan", "start", "accept"]
                case unreachable:
                    assert_never(unreachable)
            elapsed = perf_counter() - started
            # Then: 양쪽 모두 동일한 실제 산출물을 만들고 실행/사용량 근거를 남긴다.
            outputs = {
                name: (context.project_root / name).read_text(encoding="utf-8")
                for name in ("marker.txt", "summary.txt")
            }
            assert outputs == {"marker.txt": "verified", "summary.txt": "complete"}
            finished = [event for event in observed if isinstance(event, ToolFinished)]
            assert all(not event.output.is_error for event in finished)
            usage = [
                event for provider in all_providers for event in provider.emitted
                if isinstance(event, Usage)
            ]
            starts = [event for event in observed if isinstance(event, ToolStarted)]
            running = [event for event in observed if isinstance(event, ToolRunning)]
            writes_run = [event for event in starts if event.name == "write"]
            assert len(writes_run) == 2
            write_ids = {event.tool_use_id for event in writes_run}
            assert len(write_ids) == 2
            assert sum(event.tool_use_id in write_ids for event in finished) == 2
            assert sum(event.tool_use_id in write_ids for event in running) == 2
            reports.append({
                "mode": mode, "success": True, "completedTasks": 2, "totalTasks": 2,
                "completionRate": 1.0, "controlActions": control_actions,
                "userInterventions": len(control_actions) + len(permissions),
                "permissionApprovals": len(permissions), "attempts": attempt_count,
                "reexecutions": reruns, "writeExecutions": len(writes_run),
                "harnessToolExecutions": len(running),
                "verificationCommandExecutions": verification_commands,
                "elapsedSeconds": elapsed, "steps": steps,
                "providerRequests": sum(len(p.requests) for p in all_providers),
                "reportedInputTokens": sum(event.input_tokens for event in usage),
                "reportedOutputTokens": sum(event.output_tokens for event in usage),
                "sourceBase": base, "acceptedCandidate": candidate,
                "output": outputs, "fixture": str(fixture),
                "toolEvents": [event.model_dump(mode="json") for event in observed],
                "permissions": [request.model_dump(mode="json") for request in permissions],
            })
        finally:
            await store.close()
    report = {
        "objective": OBJECTIVE, "verifyCommand": VERIFY,
        "scope": "Actual local core, real tools/Git/SQLite; deterministic fake provider only.",
        "limits": "One fixed task, no quality or speed superiority claim. Planning text generation "
                  "excluded; supplied team plan. Token usage is fake-provider-reported synthetic "
                  "usage. Interventions count explicit controls and allow-once replies.",
        "results": reports,
    }
    destination = Path(os.environ.get("PERDIX_BENCHMARK_REPORT", str(tmp_path / "benchmark.json")))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"BENCHMARK_REPORT={destination}")
    print(json.dumps([{k: v for k, v in row.items() if k not in {"toolEvents", "permissions"}}
                      for row in reports], ensure_ascii=False))
