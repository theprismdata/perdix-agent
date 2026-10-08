"""task 도구: 역할 프리셋으로 서브에이전트를 스폰한다 (harness-loop.md §8, architecture.md §6.8).

kind는 "other"라 읽기 전용 레지스트리에서 빠진다. 그래서 explore/reviewer 같은 읽기 전용
서브에이전트는 다시 서브에이전트를 띄울 수 없다. explore/reviewer 호출만 읽기 전용으로 판정해
병렬 실행되고, executor는 장벽으로 직렬 실행된다.
"""

from __future__ import annotations

from typing import Any

from perdix.tools.base import ToolContext, ToolKind, ToolOutput, tool_error

# 모델이 고를 수 있는 프리셋. compactor/titler는 내부 전용이라 노출하지 않는다.
TASK_PRESETS = ("explore", "executor", "reviewer")
READ_ONLY_PRESETS = frozenset({"explore", "reviewer"})


class TaskTool:
    name = "task"
    description = (
        "Delegate a self-contained task to a subagent with its own isolated context. "
        "Presets: 'explore' (fast read-only codebase search), 'reviewer' (read-only review), "
        "'executor' (can edit files and run commands). The subagent does not see this "
        "conversation, so the prompt must contain everything it needs. "
        "Optional 'model' overrides the preset's default model. "
        "Returns the subagent's final answer."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "preset": {"type": "string", "enum": list(TASK_PRESETS)},
            "prompt": {"type": "string", "minLength": 1},
            "model": {"type": "string"},
        },
        "required": ["preset", "prompt"],
        "additionalProperties": False,
    }
    kind: ToolKind = "other"

    def is_read_only(self, input: dict[str, Any]) -> bool:
        return input.get("preset") in READ_ONLY_PRESETS

    async def run(self, ctx: ToolContext, input: dict[str, Any]) -> ToolOutput:
        if ctx.spawn_subagent is None:
            return tool_error("nested subagent spawning is not available in this context")
        try:
            outcome = await ctx.spawn_subagent(input["preset"], input["prompt"], input.get("model"))
        except KeyError as exc:
            return tool_error(str(exc))
        if outcome.stop_reason != "end_turn":
            return tool_error(f"subagent stopped early ({outcome.stop_reason}): {outcome.text}")
        return ToolOutput(content=outcome.text)
