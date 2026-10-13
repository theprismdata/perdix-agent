import os
import time
import uuid
from pathlib import Path

from perdix.memory.schema import MemorySnapshot, RawMemory


class MemoryArchive:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def snapshot(self) -> MemorySnapshot:
        paths = sorted((self.root / "snapshots").glob("*.json"))
        if not paths:
            return MemorySnapshot()
        return MemorySnapshot.model_validate_json(paths[-1].read_text(encoding="utf-8"))

    def raw(self, source_id: str) -> RawMemory | None:
        path = self.root / "raw" / f"{source_id}.json"
        if not path.is_file():
            return None
        return RawMemory.model_validate_json(path.read_text(encoding="utf-8"))

    def save_raw(self, raw: RawMemory) -> None:
        self._publish(self.root / "raw" / f"{raw.source_id}.json", raw.model_dump_json())

    def save_snapshot(self, snapshot: MemorySnapshot) -> None:
        name = f"{time.time_ns():020d}-{uuid.uuid4().hex}.json"
        self._publish(self.root / "snapshots" / name, snapshot.model_dump_json())

    def _publish(self, path: Path, content: str) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def context(self, cwd: Path) -> str:
        project = str(cwd.resolve())
        entries = [entry for entry in self.snapshot().entries
                   if entry.project_path is None or entry.project_path == project]
        text = "\n".join(
            f"[{entry.project_path or 'global'}; 출처 {', '.join(entry.sources)}] {entry.text}"
            for entry in entries
        )[-16_000:]
        if not text:
            return ""
        return (
            "자동 메모리: 과거 세션에서 추출한 참고 자료입니다. 현재 요청·권한 정책을 "
            "우선하고 오래된 내용은 현재 코드로 확인하세요.\n" + text
        )
