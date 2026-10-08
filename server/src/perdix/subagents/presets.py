"""서브에이전트 프리셋: 역할별 모델/effort/도구 범위 (architecture.md §6.8, §6.10).

프리셋마다 독립된 `Mode`(읽기 전용 여부)를 들고 있다. `TurnRunner`가 `mode.read_only`를
보고 레지스트리를 좁히므로, 프리셋 쪽에서 도구를 따로 필터링하지 않는다.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel

from perdix.harness.modes import Mode

PresetId = Literal["explore", "executor", "reviewer", "compactor", "titler"]


class SubagentPreset(BaseModel):
    id: PresetId
    model: str
    effort: str | None
    mode: Mode
    description: str


def _mode(preset_id: str, *, read_only: bool) -> Mode:
    return Mode(
        id=preset_id,
        name=preset_id,
        description=f"subagent preset: {preset_id}",
        read_only=read_only,
        verification=False,
    )


PRESETS: Final[dict[PresetId, SubagentPreset]] = {
    "explore": SubagentPreset(
        id="explore",
        model="claude-haiku-4-5",
        effort=None,
        mode=_mode("explore", read_only=True),
        description="읽기 전용 탐색/요약. 양이 많고 판단은 얕다.",
    ),
    "executor": SubagentPreset(
        id="executor",
        model="claude-opus-5-5",
        effort="high",
        mode=_mode("executor", read_only=False),
        description="부모가 쪼개준 과업 실행. 쓰기 가능, main보다 한 단계 낮은 effort.",
    ),
    "reviewer": SubagentPreset(
        id="reviewer",
        model="claude-sonnet-5-5",
        effort="medium",
        mode=_mode("reviewer", read_only=True),
        description="완료 조건 판정. 읽기 전용, 검증 게이트용.",
    ),
    "compactor": SubagentPreset(
        id="compactor",
        model="claude-haiku-4-5",
        effort=None,
        mode=_mode("compactor", read_only=True),
        description="히스토리 요약 전용, 빈번 호출.",
    ),
    "titler": SubagentPreset(
        id="titler",
        model="claude-haiku-4-5",
        effort=None,
        mode=_mode("titler", read_only=True),
        description="세션 제목 등 단발 저비용 잡무.",
    ),
}


def get(preset_id: str) -> SubagentPreset:
    """프리셋 id로 조회. 모르는 id면 KeyError."""
    try:
        return PRESETS[preset_id]  # type: ignore[index]
    except KeyError:
        raise KeyError(f"unknown subagent preset: {preset_id}") from None


def all_presets() -> list[SubagentPreset]:
    return list(PRESETS.values())
