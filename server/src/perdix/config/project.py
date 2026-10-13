"""신뢰된 목표 루프의 프로젝트 설정과 변경 감지."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class VerifyCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=200)
    run: str = Field(min_length=1, max_length=20_000)
    timeout: float = Field(default=120, gt=0, le=3600, allow_inf_nan=False)


class VerifyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    commands: list[VerifyCommand] = Field(default_factory=list, max_length=20)


class LoopLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_iterations: int = Field(default=5, ge=1, le=100)
    max_steps: int = Field(default=200, ge=1, le=2000)
    timeout: float = Field(default=600, gt=0, le=86400, allow_inf_nan=False)
    max_stagnant_iterations: int = Field(default=3, ge=1, le=100)


class ProjectConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    verify: VerifyConfig = Field(default_factory=VerifyConfig)
    loop: LoopLimits = Field(default_factory=LoopLimits)


def config_path(cwd: Path) -> Path:
    return cwd / ".perdix" / "config.json"


def config_hash(cwd: Path) -> str:
    return hashlib.sha256(config_path(cwd).read_bytes()).hexdigest()


def load_project(cwd: Path) -> tuple[ProjectConfig, str]:
    """설정과 해시는 같은 바이트에서 읽어 변경 경합을 피한다."""
    raw = config_path(cwd).read_bytes()
    return ProjectConfig.model_validate_json(raw), hashlib.sha256(raw).hexdigest()
