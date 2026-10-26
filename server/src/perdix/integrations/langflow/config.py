"""Langflow 연동 설정. 기본은 꺼짐이며 허용 목록에 있는 플로우만 호출할 수 있다."""

from __future__ import annotations

import ipaddress
import re
import stat
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

FlowAlias = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
FlowId = Annotated[
    str,
    StringConstraints(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    ),
]
_VERSION = re.compile(r"^\d+(\.\d+){0,2}$")


def parse_version(text: str) -> tuple[int, int, int]:
    """`1.13.0`, `1.13.0.post1` 같은 문자열의 숫자 앞 3자리. 못 읽으면 ValueError."""
    parts = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", text.strip())
    if parts is None:
        raise ValueError(f"버전을 읽을 수 없습니다: {text!r}")
    major, minor, patch = (int(part or 0) for part in parts.groups())
    return major, minor, patch


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class LangflowConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: bool = False
    base_url: str = "http://127.0.0.1:7860"
    # loopback이 아닌 주소는 이 값을 켜야 한다(기본은 같은 컴퓨터의 Langflow만).
    allow_remote: bool = False
    # 한 줄짜리 API 키 파일. 소유자만 읽을 수 있어야 한다(권한 600).
    api_key_file: Path | None = None
    # 도구가 부를 수 있는 플로우: 별칭 → 플로우 UUID. 여기에 없는 플로우는 호출하지 않는다.
    flows: dict[FlowAlias, FlowId] = Field(default_factory=dict, max_length=64)
    # 별칭별 모델 안내문(도구 설명에 들어간다)
    flow_descriptions: dict[FlowAlias, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=60, gt=0, le=600, allow_inf_nan=False)
    max_input_chars: int = Field(default=20_000, ge=1, le=200_000)
    max_output_chars: int = Field(default=30_000, ge=1_000, le=500_000)
    # 지원하는 Langflow 버전 범위: min 이상, max 미만
    min_version: str = "1.13.0"
    max_version: str = "2.0.0"
    # 연속 연결 실패가 이 횟수에 닿으면 breaker_seconds 동안 호출하지 않는다
    breaker_failures: int = Field(default=3, ge=1, le=20)
    breaker_seconds: float = Field(default=30, gt=0, le=3600, allow_inf_nan=False)

    @model_validator(mode="after")
    def _check(self) -> LangflowConfig:
        parts = urlsplit(self.base_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
            raise ValueError("base_url은 인증 정보가 없는 http(s) 주소여야 합니다")
        if parts.query or parts.fragment or parts.path not in {"", "/"}:
            raise ValueError("base_url에는 경로·쿼리를 넣지 않습니다")
        if not self.allow_remote and not _is_loopback(parts.hostname):
            raise ValueError("loopback이 아닌 Langflow 주소는 allow_remote가 필요합니다")
        for text in (self.min_version, self.max_version):
            if not _VERSION.match(text):
                raise ValueError("버전은 1.13.0 형식이어야 합니다")
        if parse_version(self.min_version) >= parse_version(self.max_version):
            raise ValueError("min_version은 max_version보다 낮아야 합니다")
        unknown = set(self.flow_descriptions) - set(self.flows)
        if unknown:
            raise ValueError(f"flows에 없는 별칭의 설명입니다: {sorted(unknown)}")
        if self.enabled and not self.flows:
            raise ValueError("enabled에는 최소 한 개의 flows 항목이 필요합니다")
        return self

    def read_api_key(self) -> str | None:
        """키 파일을 읽는다. 그룹·다른 사용자가 읽을 수 있으면 거부한다."""
        if self.api_key_file is None:
            return None
        path = self.api_key_file.expanduser()
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise OSError(f"API 키 파일 권한이 넓습니다(600이어야 함): {path}")
        key = path.read_text(encoding="utf-8").strip()
        if not key:
            raise OSError(f"API 키 파일이 비어 있습니다: {path}")
        return key
