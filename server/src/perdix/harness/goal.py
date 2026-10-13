from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass

from perdix.context.messages import TextBlock
from perdix.harness.events import HarnessEvent, TextDelta, ToolFailed, TurnEnded
from perdix.harness.goal_state import LoopState
from perdix.harness.modes import BUILD
from perdix.harness.turn import TurnResult, TurnRunner
from perdix.harness.verifier import LoopCallbacks, unchanged, verify
from perdix.llm.base import ProviderError


@dataclass(frozen=True, slots=True)
class GoalRunner:
    turn: TurnRunner
    callbacks: LoopCallbacks

    async def run(self, state: LoopState) -> TurnResult:
        turn, cb = self.turn, self.callbacks
        original_mode = turn.mode
        original_budget = turn.budget
        original_on_step = turn.on_step
        turn.mode = BUILD
        turn.budget = original_budget.model_copy(
            update={
                "shared_steps_limit": state.config.loop.max_steps,
                "steps_used": state.steps_used,
                "permission_denied": False,
            }
        )
        started = time.monotonic()
        prior_elapsed = state.elapsed
        denied = False

        async def sink(event: HarnessEvent) -> None:
            nonlocal denied
            if isinstance(event, TurnEnded):
                return
            if turn.budget.permission_denied or (
                isinstance(event, ToolFailed)
                and event.reason
                in (
                    "denied by policy",
                    "user rejected",
                )
            ):
                denied = True
                cb.cancel.set()
            await cb.sink(event)

        async def save() -> None:
            nonlocal state
            state = state.model_copy(
                update={
                    "elapsed": prior_elapsed + time.monotonic() - started,
                    "steps_used": turn.budget.steps_used,
                }
            )
            await turn.store.append(cb.session_id, "loop_state", state)

        async def stop(reason: str) -> TurnResult:
            nonlocal state
            state = state.model_copy(update={"status": "stopped", "reason": reason})
            await save()
            await cb.sink(TextDelta(text=state.describe(False)))
            return TurnResult(
                stop_reason="cancelled" if reason == "cancelled" else "end_turn",
                verification="failed",
            )

        try:
            turn.on_step = save
            remaining = state.config.loop.timeout - state.elapsed
            if remaining <= 0:
                return await stop("time_limit")
            state = state.model_copy(update={"status": "running", "reason": "executing"})
            await save()
            async with asyncio.timeout(remaining):
                while state.iteration < state.config.loop.max_iterations:
                    if cb.cancel.is_set():
                        return await stop("cancelled")
                    if not unchanged(state, turn):
                        return await stop("config_changed")
                    if turn.budget.steps_used >= state.config.loop.max_steps:
                        return await stop("step_limit")
                    state = state.model_copy(update={"iteration": state.iteration + 1})
                    await save()
                    await cb.sink(TextDelta(text=state.describe(True)))
                    prompt = f"목표: {state.goal}\n구현 후 검증은 서버가 실행합니다."
                    if state.feedback:
                        prompt += f"\n이전 검증 실패를 해결하세요:\n{state.feedback}"
                    result = await turn.run(
                        cb.session_id,
                        [TextBlock(text=prompt)],
                        cancel=cb.cancel,
                        sink=sink,
                        ask_permission=cb.ask_permission,
                        origin="synthetic",
                    )
                    await save()
                    if denied:
                        return await stop("permission_denied")
                    if cb.cancel.is_set() or result.stop_reason == "cancelled":
                        return await stop("cancelled")
                    if not unchanged(state, turn):
                        return await stop("config_changed")
                    if result.stop_reason != "end_turn":
                        reason = (
                            "step_limit"
                            if result.stop_reason == "max_turn_requests"
                            else result.stop_reason
                        )
                        return await stop(reason)
                    checked = await verify(state, turn, cb)
                    state = state.model_copy(update={"feedback": checked.feedback})
                    if checked.terminal_reason is not None:
                        return await stop(checked.terminal_reason)
                    if checked.passed:
                        state = state.model_copy(update={"status": "passed", "reason": "verified"})
                        await save()
                        await cb.sink(TextDelta(text=state.describe(False)))
                        return TurnResult(stop_reason="end_turn", verification="passed")
                    digest = hashlib.sha256(checked.feedback.encode()).hexdigest()
                    stagnant = state.stagnant_iterations + 1 if digest == state.failure_hash else 1
                    state = state.model_copy(
                        update={
                            "failure_hash": digest,
                            "stagnant_iterations": stagnant,
                        }
                    )
                    await save()
                    if stagnant >= state.config.loop.max_stagnant_iterations:
                        return await stop("no_progress")
                return await stop("iteration_limit")
        except TimeoutError:
            return await stop("time_limit")
        except ProviderError as exc:
            state = state.model_copy(update={"feedback": str(exc)})
            return await stop("provider_error")
        finally:
            if state.status == "running":
                state = state.model_copy(update={"status": "stopped", "reason": "interrupted"})
                await save()
            turn.mode = original_mode
            turn.budget = original_budget
            turn.on_step = original_on_step
