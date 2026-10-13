from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import assert_never

from perdix.config.project import config_hash
from perdix.harness.events import (
    PermissionRequest,
    ToolFailed,
    ToolFinished,
    ToolRunning,
    ToolStarted,
)
from perdix.harness.goal_state import LoopState
from perdix.harness.turn import AskPermission, Sink, TurnRunner
from perdix.tools.base import ToolOutput


@dataclass(frozen=True, slots=True)
class LoopCallbacks:
    session_id: str
    cancel: asyncio.Event
    sink: Sink
    ask_permission: AskPermission


@dataclass(frozen=True, slots=True)
class VerificationResult:
    passed: bool
    feedback: str
    terminal_reason: str | None = None


def unchanged(state: LoopState, turn: TurnRunner) -> bool:
    try:
        return config_hash(turn.cwd) == state.config_hash
    except OSError:
        return False


async def verify(
    state: LoopState, turn: TurnRunner, callbacks: LoopCallbacks
) -> VerificationResult:
    reports: list[str] = []
    for command in state.config.verify.commands:
        if callbacks.cancel.is_set():
            return VerificationResult(False, "검증 취소", "cancelled")
        if not unchanged(state, turn):
            return VerificationResult(False, "프로젝트 설정이 변경되었습니다.", "config_changed")
        call_id = f"verify_{uuid.uuid4().hex}"
        input = {"command": command.run}
        title = f"verify: {command.name}"
        await callbacks.sink(
            ToolStarted(tool_use_id=call_id, name="bash", kind="execute", title=title, input=input)
        )
        decision = turn.policy.evaluate("bash", input)
        match decision:
            case "deny":
                await callbacks.sink(ToolFailed(tool_use_id=call_id, reason="검증 권한 거부"))
                return VerificationResult(
                    False, "검증 명령의 실행 권한이 없습니다.", "permission_denied"
                )
            case "ask":
                request = PermissionRequest(
                    tool_use_id=call_id, name="bash", kind="execute", title=title, input=input
                )
                outcome = await turn._ask_until_cancelled(
                    callbacks.ask_permission, request, callbacks.cancel
                )
                await turn.store.append(
                    callbacks.session_id,
                    "permission_decision",
                    {
                        "tool_use_id": call_id,
                        "tool_name": "bash",
                        "decision": outcome.outcome,
                        "command": command.run,
                    },
                )
                match outcome.outcome:
                    case "cancelled":
                        await callbacks.sink(ToolFailed(tool_use_id=call_id, reason="검증 취소"))
                        return VerificationResult(False, "검증 취소", "cancelled")
                    case "reject_once" | "reject_always":
                        if outcome.outcome == "reject_always":
                            turn.policy.remember("bash", input, "deny")
                        await callbacks.sink(
                            ToolFailed(tool_use_id=call_id, reason="검증 권한 거부")
                        )
                        return VerificationResult(False, "검증 권한 거부", "permission_denied")
                    case "allow_once":
                        pass
                    case "allow_always":
                        turn.policy.remember("bash", input, "allow")
                    case unreachable:
                        assert_never(unreachable)
            case "allow":
                pass
            case unreachable_decision:
                assert_never(unreachable_decision)
        if not unchanged(state, turn):
            await callbacks.sink(ToolFailed(tool_use_id=call_id, reason="프로젝트 설정 변경"))
            return VerificationResult(False, "프로젝트 설정 변경", "config_changed")
        await callbacks.sink(ToolRunning(tool_use_id=call_id))
        try:
            result = await turn.sandbox.run(
                command.run, cwd=turn.cwd, timeout=command.timeout, cancel=callbacks.cancel
            )
        except asyncio.CancelledError:
            await callbacks.sink(ToolFailed(tool_use_id=call_id, reason="검증 중단"))
            raise
        except OSError as exc:
            await callbacks.sink(ToolFailed(tool_use_id=call_id, reason=str(exc)))
            return VerificationResult(False, str(exc), "verification_error")
        passed = result.exit_code == 0 and not result.timed_out and not result.cancelled
        report = (
            f"{command.name}: exit={result.exit_code}, timeout={result.timed_out}\n"
            f"{result.stdout[-4000:]}\n{result.stderr[-4000:]}"
        )
        reports.append(report)
        await callbacks.sink(
            ToolFinished(
                tool_use_id=call_id, output=ToolOutput(content=report, is_error=not passed)
            )
        )
        if result.cancelled or callbacks.cancel.is_set():
            return VerificationResult(False, report, "cancelled")
        if not unchanged(state, turn):
            return VerificationResult(False, report, "config_changed")
        if not passed:
            return VerificationResult(False, "\n".join(reports))
    return VerificationResult(True, "\n".join(reports))
