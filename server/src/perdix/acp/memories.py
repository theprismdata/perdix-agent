from pathlib import Path

from acp import RequestError
from acp.schema import SessionConfigOptionSelect, SessionConfigSelectOption
from pydantic import ValidationError

from perdix.context.builder import build_system_prompt
from perdix.harness.modes import Mode
from perdix.memory.archive import MemoryArchive
from perdix.memory.schema import MemoryConfig
from perdix.memory.source import chat_setting
from perdix.memory.worker import MemoryWorker
from perdix.session.store import Event, EventStore

MEMORY_OPTION_IDS = frozenset({"memory_use", "memory_generate"})


class SessionMemories:
    def __init__(self, config: MemoryConfig) -> None:
        self.config = config
        self.worker: MemoryWorker | None = None
        self._use: dict[str, bool] = {}
        self._generate: dict[str, bool] = {}

    def restore(self, session_id: str, events: list[Event]) -> None:
        self._use[session_id] = chat_setting(events, "memory_use", True)
        self._generate[session_id] = chat_setting(events, "memory_generate", True)

    def prompt(self, session_id: str, cwd: Path, mode: Mode) -> str:
        config = self.config.model_copy(update={
            "use_memories": self.config.use_memories and self._use.get(session_id, True),
        })
        return build_system_prompt(cwd, mode, config)

    def options(self, session_id: str) -> list[SessionConfigOptionSelect]:
        result: list[SessionConfigOptionSelect] = []
        for key, name, values, allowed in (
            ("memory_use", "메모리 참조", self._use, self.config.use_memories),
            ("memory_generate", "자동 메모리 생성", self._generate, self.config.generate_memories),
        ):
            enabled = self.config.enabled and allowed and values.get(session_id, True)
            result.append(SessionConfigOptionSelect(
                type="select", id=key, name=name,
                current_value="on" if enabled else "off",
                options=[SessionConfigSelectOption(value="on", name="켜기"),
                         SessionConfigSelectOption(value="off", name="끄기")],
            ))
        return result

    async def set_option(
        self, store: EventStore, session_id: str, key: str, value: str | bool,
    ) -> None:
        if key not in MEMORY_OPTION_IDS or value not in ("on", "off"):
            raise RequestError.invalid_params(
                {"configId": key, "reason": "on 또는 off가 필요합니다"}
            )
        enabled = value == "on"
        await store.append(session_id, "config_change", {"key": key, "value": enabled})
        values = self._use if key == "memory_use" else self._generate
        values[session_id] = enabled

    async def command(self, store: EventStore, session_id: str, text: str) -> str | None:
        words = text.strip().split()
        if not words or words[0] != "/memories":
            return None
        if len(words) == 3 and words[1] in ("use", "generate") and words[2] in ("on", "off"):
            await self.set_option(store, session_id, "memory_" + words[1], words[2])
        elif len(words) > 1:
            return "사용법: /memories | /memories use on|off | /memories generate on|off\n"
        use = self.config.enabled and self.config.use_memories and self._use.get(session_id, True)
        generate = (self.config.enabled and self.config.generate_memories
                    and self._generate.get(session_id, True))
        try:
            count = len(MemoryArchive(self.config.root).snapshot().entries)
            stored = f"저장된 자동 메모리: {count}개"
        except (OSError, ValidationError):
            stored = "저장된 자동 메모리를 읽을 수 없습니다"
        return (
            f"메모리 참조: {'켜짐' if use else '꺼짐'}\n"
            f"자동 생성: {'켜짐' if generate else '꺼짐'}\n"
            f"유휴 기준: {self.config.min_idle_hours:g}시간\n저장소: {self.config.root}\n"
            f"{stored}\n"
        )
