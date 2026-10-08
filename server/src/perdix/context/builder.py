"""시스템 프롬프트 조립. 캐시 프리픽스 안정성을 위해 가변 정보(시간 등)를 넣지 않는다."""

from __future__ import annotations

from pathlib import Path

from perdix.harness.modes import Mode

_BASE_INSTRUCTIONS = """\
You are Perdix, a coding agent working in the user's project directory.
- Use the provided tools to read, search, edit files and run commands.
- Read a file before editing it. Keep changes minimal and focused on the request.
- Tool results marked as errors explain what went wrong; adjust and continue.
- When the task is done, reply with a short summary of what you changed."""


def build_system_prompt(cwd: Path | str, mode: Mode) -> str:
    lines = [
        _BASE_INSTRUCTIONS,
        "",
        f"Working directory: {Path(cwd)}",
        f"Mode: {mode.name} ({mode.id}) - {mode.description}",
    ]
    if mode.read_only:
        lines.append("This mode is read-only: do not attempt to modify files.")
    return "\n".join(lines)
