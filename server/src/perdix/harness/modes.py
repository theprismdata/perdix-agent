"""세션 모드: chat, plan, build (m1.md §4)."""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel


class Mode(BaseModel):
    id: str
    name: str
    description: str
    # True면 읽기 전용 도구만 모델에 노출한다.
    read_only: bool
    # True면 Turn 종료 전 검증 게이트 대상이다(M1은 게이트 없이 "skipped").
    verification: bool


CHAT: Final = Mode(
    id="chat",
    name="Chat",
    description="Answer questions and explore the codebase. Read-only tools only.",
    read_only=True,
    verification=False,
)
PLAN: Final = Mode(
    id="plan",
    name="Plan",
    description="Investigate and propose a plan without modifying files. Read-only tools only.",
    read_only=True,
    verification=False,
)
BUILD: Final = Mode(
    id="build",
    name="Build",
    description="Implement changes: read, edit, and run commands, then verify the result.",
    read_only=False,
    verification=True,
)

MODES: Final[dict[str, Mode]] = {m.id: m for m in (CHAT, PLAN, BUILD)}
