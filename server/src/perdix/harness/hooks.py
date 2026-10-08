"""도구 실행 전후 훅 (harness-loop.md §3.3 규칙 5).

`pre_tool(name, input) -> allow | deny | mutate(input)`,
`post_tool(name, input, result) -> result'`. 등록 순서대로 호출한다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel

from perdix.tools.base import ToolOutput


class HookAllow(BaseModel):
    action: Literal["allow"] = "allow"


class HookDeny(BaseModel):
    action: Literal["deny"] = "deny"
    reason: str = "denied by hook"


class HookMutate(BaseModel):
    action: Literal["mutate"] = "mutate"
    input: dict[str, Any]


PreToolDecision = HookAllow | HookDeny | HookMutate
PreToolHook = Callable[[str, dict[str, Any]], Awaitable[PreToolDecision]]
PostToolHook = Callable[[str, dict[str, Any], ToolOutput], Awaitable[ToolOutput]]


class HookBus:
    def __init__(self) -> None:
        self.pre_tool: list[PreToolHook] = []
        self.post_tool: list[PostToolHook] = []

    async def run_pre_tool(self, name: str, input: dict[str, Any]) -> PreToolDecision:
        """deny는 즉시 반환. mutate는 바뀐 입력을 다음 훅에 넘기고 최종 결과로 돌려준다."""
        current = input
        mutated = False
        for hook in self.pre_tool:
            decision = await hook(name, current)
            if isinstance(decision, HookDeny):
                return decision
            if isinstance(decision, HookMutate):
                current = decision.input
                mutated = True
        return HookMutate(input=current) if mutated else HookAllow()

    async def run_post_tool(
        self, name: str, input: dict[str, Any], output: ToolOutput
    ) -> ToolOutput:
        for hook in self.post_tool:
            output = await hook(name, input, output)
        return output
