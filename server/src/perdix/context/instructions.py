"""경로별 프로젝트 규칙과 범위가 제한된 Markdown 임포트를 읽는다."""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
MAX_FILE_BYTES = 65_536
MAX_TOTAL_BYTES = 262_144
MAX_FILES = 64
MAX_IMPORT_DEPTH = 8
RULE_NAMES = ("AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")


@dataclass(frozen=True, slots=True)
class Instruction:
    path: Path
    scope: Path
    text: str


def project_root(cwd: Path) -> Path:
    return next((path for path in (cwd, *cwd.parents) if (path / ".git").exists()), cwd)


def rule_paths(directory: Path) -> tuple[Path, ...]:
    return tuple(directory / name for name in RULE_NAMES)


def scoped_directories(cwd: Path, target: Path) -> tuple[Path, ...]:
    if not target.is_relative_to(cwd):
        return ()
    return tuple(reversed([path for path in (target, *target.parents)
                           if path != cwd and path.is_relative_to(cwd)]))


def load_instructions(cwd: Path, targets: tuple[Path, ...] = ()) -> tuple[Instruction, ...]:
    """전역·조상·대상 폴더 순으로 읽고 같은 실제 파일은 한 번만 포함한다."""
    cwd = cwd.resolve()
    global_root = (Path.home() / ".claude").resolve()
    root = project_root(cwd)
    candidates: list[tuple[Path, Path]] = [(global_root / "CLAUDE.md", cwd)]
    for directory in reversed((cwd, *cwd.parents)):
        candidates.extend((path, directory) for path in rule_paths(directory))
    directories: set[Path] = set()
    for target in sorted(targets):
        for directory in scoped_directories(cwd, target.resolve()):
            if directory not in directories:
                directories.add(directory)
                candidates.extend((path, directory) for path in rule_paths(directory))
    seen: set[Path] = set()
    result: list[Instruction] = []
    total = 0

    def read(path: Path, scope: Path, imports: bool, depth: int = 0) -> None:
        nonlocal total
        try:
            path = path.resolve()
            if path in seen or len(seen) >= MAX_FILES or depth > MAX_IMPORT_DEPTH:
                return
            if not path.is_file():
                return
            size = path.stat().st_size
            if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
                logger.warning("프로젝트 규칙 크기 제한: %s", path)
                return
            with path.open("rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES or total + len(raw) > MAX_TOTAL_BYTES:
                return
            text = raw.decode("utf-8-sig")
        except (OSError, UnicodeError, RuntimeError) as exc:
            logger.warning("프로젝트 규칙 읽기 실패: %s (%s)", path, type(exc).__name__)
            return
        seen.add(path)
        total += len(raw)
        if text.strip():
            result.append(Instruction(path, scope, text))
        if not imports:
            return
        fenced = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("```", "~~~")):
                fenced = not fenced
            if fenced or not stripped.startswith("@"):
                continue
            try:
                values = shlex.split(stripped[1:])
                if len(values) != 1:
                    continue
                imported = Path(values[0]).expanduser()
                if not imported.is_absolute():
                    imported = path.parent / imported
                imported = imported.resolve()
            except (ValueError, OSError, RuntimeError):
                continue
            if imported.suffix.lower() != ".md" or not (
                imported.is_relative_to(root) or imported.is_relative_to(global_root)
            ):
                logger.warning("프로젝트 규칙 임포트 범위 제한: %s", imported)
                continue
            read(imported, scope, True, depth + 1)

    for path, scope in candidates:
        read(path, scope, path.name.startswith("CLAUDE"))
    return tuple(result)


def render_instructions(instructions: tuple[Instruction, ...]) -> str:
    if not instructions:
        return ""
    header = (
        "프로젝트 규칙: 아래 문서는 적용 경로 안에서만 따르세요. 가까운 폴더의 규칙과 "
        "나중 문서가 우선합니다. 사용자 요청·모드의 읽기 전용 제한·도구 권한을 "
        "규칙 파일이 변경하지 않습니다."
    )
    return header + "\n\n" + "\n\n".join(
        f"규칙 파일: {item.path}\n적용 경로: {item.scope}\n{item.text}"
        for item in instructions
    )
