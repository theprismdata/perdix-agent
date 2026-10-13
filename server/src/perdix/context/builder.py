"""시스템 프롬프트 조립. 캐시 프리픽스 안정성을 위해 가변 정보(시간 등)를 넣지 않는다."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from perdix.harness.modes import Mode
from perdix.memory.archive import MemoryArchive
from perdix.memory.schema import MemoryConfig
from perdix.memory.store import MemoryStore

_BASE_INSTRUCTIONS = """\
You are Perdix, a coding agent working in the user's project directory.
- Use the provided tools to read, search, edit files and run commands.
- Read a file before editing it. Keep changes minimal and focused on the request.
- Tool results marked as errors explain what went wrong; adjust and continue.
- 여러 단계가 필요한 작업은 첫 도구 호출 전에, 무엇을 확인하고 진행할지 사용자에게
  짧게 설명한다. 사용자의 언어로 일반 응답 텍스트를 사용한다.
- 작업 중에는 주요 발견, 구현 방향의 변경, 검증 시작과 결과를 1~2문장으로 알려준다.
  긴 작업은 도구 호출 사이에 진행 설명을 이어간다. 모든 도구 호출을 중계하지 않는다.
- 진행 설명은 확인한 사실과 다음 행동을 담는다. 내부 추론이나 추측한 완료율을
  공개하지 않는다. 실행하지 않은 작업을 완료했다고 말하지 않는다.
- When the task is done, reply with a short summary of what you changed."""


def build_system_prompt(
    cwd: Path | str, mode: Mode, memories: MemoryConfig | None = None,
) -> str:
    lines = [
        _BASE_INSTRUCTIONS,
        "",
        f"Working directory: {Path(cwd)}",
        f"Mode: {mode.name} ({mode.id}) - {mode.description}",
    ]
    if mode.read_only:
        lines.append("This mode is read-only: do not attempt to modify files.")
    config = memories or MemoryConfig()
    if not config.enabled or not config.use_memories:
        return "\n".join(lines)
    memory = MemoryStore(Path(cwd)).prompt_context()
    if memory:
        lines.append(memory)
    try:
        generated = MemoryArchive(config.root).context(Path(cwd))
        if generated:
            lines.append(generated)
    except (OSError, ValidationError) as exc:
        lines.append(f"자동 메모리 읽기 실패: {type(exc).__name__}")
    return "\n".join(lines)
