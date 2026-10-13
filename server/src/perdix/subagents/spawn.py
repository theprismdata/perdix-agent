"""서브에이전트 스폰: 격리된 하위 세션 생성 → 실행 → 결과 수집 (architecture.md §6.8).

부모 히스토리는 공유하지 않는다. 과업 설명(`task`)만 새 세션의 첫 user_message로 들어간다.
권한 규칙(`policy`)과 예산(`budget`)은 호출자가 부모 것을 그대로 넘겨 상속한다.
모델/effort/읽기전용 여부는 프리셋(`subagents.presets`)이 정한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from perdix.config.agents import AgentsConfig
from perdix.context.builder import build_system_prompt
from perdix.context.messages import AssistantMessage, TextBlock
from perdix.harness.budget import Budget
from perdix.harness.events import HarnessEvent, PermissionOutcome, PermissionRequest
from perdix.harness.hooks import HookBus
from perdix.harness.turn import DEFAULT_MAX_TOKENS, SystemPromptBuilder, TurnRunner
from perdix.llm.base import Provider
from perdix.permissions.policy import PermissionPolicy
from perdix.sandbox.base import ExecBackend
from perdix.session.store import EventStore
from perdix.subagents.presets import get as get_preset
from perdix.tools.registry import ToolRegistry

ProviderFactory = Callable[[str], Provider]
Sink = Callable[[HarnessEvent], Awaitable[None]]
AskPermission = Callable[[PermissionRequest], Awaitable[PermissionOutcome]]


async def _deny_ask(_: PermissionRequest) -> PermissionOutcome:
    """서브에이전트에는 대화형 승인 채널이 없다. ask 판정은 기본적으로 거부한다."""
    return PermissionOutcome(outcome="reject_once")


async def _ignore_event(_: HarnessEvent) -> None:
    return None


@dataclass
class SubagentOutcome:
    session_id: str
    stop_reason: str
    text: str


async def _final_text(store: EventStore, session_id: str) -> str:
    """마지막 assistant_message의 텍스트. 없으면 빈 문자열."""
    events = await store.events(session_id)
    for event in reversed(events):
        if event.kind != "assistant_message":
            continue
        return AssistantMessage.model_validate(event.payload).text()
    return ""


async def spawn_subagent(
    preset_id: str,
    task: str,
    *,
    project_path: str,
    cwd: Path,
    store: EventStore,
    registry: ToolRegistry,
    policy: PermissionPolicy,
    sandbox: ExecBackend,
    budget: Budget,
    hooks: HookBus | None = None,
    provider_factory: ProviderFactory,
    system_prompt_builder: SystemPromptBuilder = build_system_prompt,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    sink: Sink = _ignore_event,
    ask_permission: AskPermission = _deny_ask,
    model: str | None = None,
    agents_config: AgentsConfig | None = None,
    on_step: Callable[[], Awaitable[None]] | None = None,
) -> SubagentOutcome:
    """`preset_id` 프리셋으로 격리된 하위 세션을 만들어 `task`를 실행하고 결과를 모은다."""
    preset = get_preset(preset_id, agents_config)
    # `model`이 주어지면 프리셋 모델(설정 오버라이드 반영 후)을 덮어쓴다(harness-loop.md §8).
    # effort는 프리셋 그대로.
    effective_model = model or preset.model
    provider = provider_factory(effective_model)
    record = await store.create_session(
        project_path, preset.mode.id, provider.name, effective_model
    )
    runner = TurnRunner(
        store=store,
        provider=provider,
        registry=registry,
        policy=policy,
        sandbox=sandbox,
        budget=budget,
        hooks=hooks or HookBus(),
        mode=preset.mode,
        cwd=cwd,
        system_prompt_builder=system_prompt_builder,
        model=effective_model,
        max_tokens=max_tokens,
        effort=preset.effort,
        on_step=on_step,
        compactor_factory=provider_factory,
        agents_config=agents_config,
    )
    result = await runner.run(
        record.id,
        [TextBlock(text=task)],
        cancel=asyncio.Event(),
        sink=sink,
        ask_permission=ask_permission,
    )
    text = await _final_text(store, record.id)
    return SubagentOutcome(session_id=record.id, stop_reason=result.stop_reason, text=text)
