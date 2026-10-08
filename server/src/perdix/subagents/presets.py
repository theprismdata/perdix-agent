"""서브에이전트 프리셋: 역할별 모델/effort/도구 범위 (architecture.md §6.8, §6.10).

프리셋마다 독립된 `Mode`(읽기 전용 여부)를 들고 있다. `TurnRunner`가 `mode.read_only`를
보고 레지스트리를 좁히므로, 프리셋 쪽에서 도구를 따로 필터링하지 않는다.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel

from perdix.config.agents import AgentsConfig
from perdix.harness.modes import Mode

PresetId = Literal[
    "explore", "executor", "reviewer", "compactor", "titler", "debugger", "security-reviewer"
]


class SubagentPreset(BaseModel):
    id: PresetId
    model: str
    effort: str | None
    mode: Mode
    description: str


def _preset(
    preset_id: PresetId, *, model: str, effort: str | None, read_only: bool, description: str
) -> SubagentPreset:
    # description은 한 번만 정의해 프리셋 메타데이터와 Mode(시스템 프롬프트에 실림) 양쪽에 쓴다.
    mode = Mode(
        id=preset_id,
        name=preset_id,
        description=description,
        read_only=read_only,
        verification=False,
    )
    return SubagentPreset(
        id=preset_id, model=model, effort=effort, mode=mode, description=description
    )


PRESETS: Final[dict[PresetId, SubagentPreset]] = {
    "explore": _preset(
        "explore",
        model="claude-haiku-4-5",
        effort=None,
        read_only=True,
        description="읽기 전용 탐색/요약. 양이 많고 판단은 얕다.",
    ),
    "executor": _preset(
        "executor",
        model="claude-opus-5-5",
        effort="high",
        read_only=False,
        description="부모가 쪼개준 과업 실행. 쓰기 가능, main보다 한 단계 낮은 effort.",
    ),
    "reviewer": _preset(
        "reviewer",
        model="claude-sonnet-5-5",
        effort="medium",
        read_only=True,
        description="완료 조건 판정. 읽기 전용, 검증 게이트용.",
    ),
    "compactor": _preset(
        "compactor",
        model="claude-haiku-4-5",
        effort=None,
        read_only=True,
        description="히스토리 요약 전용, 빈번 호출.",
    ),
    "titler": _preset(
        "titler",
        model="claude-haiku-4-5",
        effort=None,
        read_only=True,
        description="세션 제목 등 단발 저비용 잡무.",
    ),
    "debugger": _preset(
        "debugger",
        model="claude-sonnet-5-5",
        effort="medium",
        read_only=False,
        description="실패 원인 분석. 재현에 필요하면 쓰기·실행 도구도 쓸 수 있다.",
    ),
    "security-reviewer": _preset(
        "security-reviewer",
        model="claude-sonnet-5-5",
        effort="medium",
        read_only=True,
        description="취약점·신뢰 경계 점검. 읽기 전용, 코드 리뷰 관점의 보안 검토.",
    ),
}


def get(preset_id: str, agents_config: AgentsConfig | None = None) -> SubagentPreset:
    """프리셋 id로 조회. 모르는 id면 KeyError.

    `agents_config`에 이 역할 오버라이드가 있으면 model/effort를 교체한다(architecture.md §6.10).
    """
    try:
        base = PRESETS[preset_id]  # type: ignore[index]
    except KeyError:
        raise KeyError(f"unknown subagent preset: {preset_id}") from None
    if agents_config is None:
        return base
    override = agents_config.agents.get(preset_id)
    if override is None:
        return base
    # model과 effort는 한 쌍으로 교체한다. 설정에 effort가 없으면 프리셋 effort를 유지하지 않고
    # None(effort 미전송)으로 덮어쓴다. 다른 모델에 기존 effort가 맞으리란 보장이 없어서다.
    return base.model_copy(
        update={"model": agents_config.resolve(override.model), "effort": override.effort}
    )


def all_presets() -> list[SubagentPreset]:
    return list(PRESETS.values())
