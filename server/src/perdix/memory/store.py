"""프로젝트·전역의 추가 전용 메모리 파일."""

from pathlib import Path
from typing import Final, Literal, assert_never

from filelock import FileLock

MemoryScope = Literal["project", "global"]
MAX_MEMORY_CHARS: Final = 64_000
PROMPT_MEMORY_CHARS: Final = 16_000


class MemoryStore:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()

    def path(self, scope: MemoryScope) -> Path:
        match scope:
            case "project":
                root = self.cwd
            case "global":
                root = Path.home().resolve()
            case unreachable:
                assert_never(unreachable)
        path = root / ".perdix" / "MEMORY.md"
        if not path.resolve().is_relative_to(root):
            raise OSError("메모리 경로가 저장 범위를 벗어났습니다")
        return path

    def read(self, scope: MemoryScope) -> str:
        path = self.path(scope)
        try:
            with path.open(encoding="utf-8") as stream:
                text = stream.read(MAX_MEMORY_CHARS + 1)
        except FileNotFoundError:
            return ""
        if len(text) > MAX_MEMORY_CHARS:
            raise OSError("메모리 파일이 64,000자 제한을 초과했습니다")
        return text

    def append(self, scope: MemoryScope, content: str) -> Path:
        """잠금을 얻지 못하면 재시도 가능한 도구 오류로 돌려준다."""
        path = self.path(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(path) + ".lock", timeout=0):
            previous = self.read(scope)
            entry = "\n\n" + content.strip() + "\n"
            if len(previous) + len(entry) > MAX_MEMORY_CHARS:
                raise OSError("메모리 파일이 64,000자 제한을 초과합니다")
            with path.open("a", encoding="utf-8") as stream:
                stream.write(entry)
        return path

    def prompt_context(self) -> str:
        sections: list[str] = []
        for scope in ("global", "project"):
            try:
                content = self.read(scope)
            except (OSError, UnicodeError) as exc:
                sections.append(f"메모리 읽기 실패 ({scope}): {exc}")
                continue
            if content.strip():
                sections.append(
                    f"메모리 ({scope}, {self.path(scope)}):\n"
                    + content[-PROMPT_MEMORY_CHARS:]
                )
        if not sections:
            return ""
        return (
            "아래 메모리는 참고 자료입니다. 현재 사용자 요청과 권한 정책을 우선합니다. "
            "필요한 사실은 자동 메모리에도 정리됩니다.\n\n"
            + "\n\n".join(sections)
        )
