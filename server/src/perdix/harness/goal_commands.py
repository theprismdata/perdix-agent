from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from perdix.config.project import load_project
from perdix.harness.goal_state import LoopState
from perdix.session.store import EventStore

LOOP_HELP = (
    "/loop <목표> — 실행·검증·수정 반복\n"
    "/loop status — 진행 상태\n/loop cancel — 중지\n/loop resume — 저장된 목표 재개\n"
    "완료 기준: .perdix/config.json의 verify.commands. CLI는 --trust-project로 실행하세요.\n"
)


def loop_argument(text: str) -> str | None:
    parts = text.strip().split(maxsplit=1)
    if not parts or parts[0] != "/loop":
        return None
    return parts[1].strip() if len(parts) == 2 else ""


async def latest_loop(store: EventStore, session_id: str) -> LoopState | None:
    for event in reversed(await store.events(session_id)):
        if event.kind == "loop_state":
            return LoopState.model_validate(event.payload)
    return None


@dataclass(frozen=True, slots=True)
class LoopCommands:
    store: EventStore
    trusted_root: Path | None

    async def prepare(self, session_id: str, argument: str, cwd: Path) -> LoopState | str:
        if self.trusted_root != cwd.resolve():
            return "프로젝트가 미신뢰 상태입니다. CLI를 --trust-project로 다시 실행하세요."
        try:
            config, digest = load_project(cwd)
        except (OSError, ValidationError) as exc:
            return f"프로젝트 검증 설정을 읽을 수 없습니다: {exc}"
        if not config.verify.commands:
            return "verify.commands에 하나 이상의 검증 명령이 필요합니다."
        if any(not c.run.strip() or not c.name.strip() for c in config.verify.commands):
            return "검증 명령의 name과 run은 빈 문자열일 수 없습니다."
        previous = await latest_loop(self.store, session_id)
        if argument == "resume":
            if previous is None:
                return "재개할 목표 루프가 없습니다."
            if previous.status == "passed":
                return "이미 검증을 통과한 목표입니다. 새 목표를 시작하세요."
            if previous.project_path != str(cwd.resolve()) or previous.config_hash != digest:
                return "프로젝트 경로나 설정이 변경되어 재개할 수 없습니다. 새 목표를 시작하세요."
            if (
                previous.iteration >= config.loop.max_iterations
                or previous.steps_used >= config.loop.max_steps
                or previous.elapsed >= config.loop.timeout
                or previous.stagnant_iterations >= config.loop.max_stagnant_iterations
            ):
                return "저장된 목표의 실행 한도가 소진되었습니다. 새 목표를 시작하세요."
            return previous.model_copy(update={"status": "running", "reason": "resumed"})
        goal = argument.removeprefix("start ").strip()
        if not goal:
            return LOOP_HELP
        return LoopState(
            id=uuid.uuid4().hex,
            goal=goal,
            project_path=str(cwd.resolve()),
            config=config,
            config_hash=digest,
        )
