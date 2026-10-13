from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MemoryConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: bool = True
    generate_memories: bool = True
    use_memories: bool = True
    root: Path = Field(default_factory=lambda: Path.home() / ".perdix" / "memories")
    min_idle_hours: float = Field(default=6, ge=0, le=48, allow_inf_nan=False)
    max_age_days: int = Field(default=30, ge=1, le=90)
    min_user_turns: int = Field(default=2, ge=2, le=100)
    poll_seconds: float = Field(default=60, gt=0, le=3600, allow_inf_nan=False)
    max_sessions_per_pass: int = Field(default=4, ge=1, le=16)
    timeout_seconds: float = Field(default=60, gt=0, le=300, allow_inf_nan=False)
    max_tokens: int = Field(default=4096, ge=256, le=8192)
    extract_model: str | None = None
    consolidation_model: str | None = None
    fake_script: Path | None = None


class MemoryFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_.-]+$")
    text: str = Field(min_length=1, max_length=1000)
    scope: Literal["project", "global"] = "project"


class Extraction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    summary: str = Field(default="", max_length=2000)
    facts: list[MemoryFact] = Field(default_factory=list, max_length=16)


class RawMemory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str
    source_seq: int
    project_path: str
    generated_at: float
    extraction: Extraction

    @property
    def source_id(self) -> str:
        return f"{self.session_id}-{self.source_seq}"


class MemoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_.-]+$")
    text: str = Field(min_length=1, max_length=1000)
    project_path: str | None = None
    sources: list[str] = Field(min_length=1, max_length=256)


class Consolidation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    entries: list[MemoryEntry] = Field(default_factory=list, max_length=100)


class MemorySnapshot(Consolidation):
    processed: dict[str, int] = Field(default_factory=dict)
    generated_at: float = 0
