"""사용자가 제출한 workflow 명령의 구문 경계."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class WorkflowCommandError(ValueError):
    """지원하지 않거나 인수가 누락된 명령."""


class WorkflowCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["plan", "start", "status", "resume", "retry", "accept", "reject"]
    request: str = ""
    run_id: str | None = None
    task_id: str | None = None


def parse_workflow_command(text: str) -> WorkflowCommand | None:
    words = text.strip().split(maxsplit=2)
    if not words or words[0] != "/workflow":
        return None
    if len(words) != 3:
        raise WorkflowCommandError("/workflow <명령> <요청 또는 runId>가 필요합니다")
    action, argument = words[1:]
    match action:
        case "plan":
            return WorkflowCommand(action="plan", request=argument)
        case "start" | "status" | "resume" | "accept" | "reject":
            if len(argument.split()) != 1:
                raise WorkflowCommandError("runId 하나가 필요합니다")
            return WorkflowCommand(action=action, run_id=argument)
        case "retry":
            arguments = argument.split()
            if len(arguments) != 2:
                raise WorkflowCommandError("retry에는 runId와 taskId가 필요합니다")
            return WorkflowCommand(action="retry", run_id=arguments[0], task_id=arguments[1])
        case _:
            raise WorkflowCommandError(f"지원하지 않는 workflow 명령: {action}")
