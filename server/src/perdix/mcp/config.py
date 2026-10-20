"""MCP 서버 설정 로더(ADR 0034).

신뢰 경계: ACP 인수와 전역 `~/.perdix/mcp.json`은 신뢰하고, 프로젝트 `.mcp.json`은
호출자가 `trust_project`를 확인한 뒤에만 읽는다(AGENTS.md 규칙 9).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

DEFAULT_GLOBAL_PATH = Path.home() / ".perdix" / "mcp.json"
SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
MAX_CONFIG_BYTES = 256 * 1024


class McpServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    command: str = Field(min_length=1, max_length=4096)
    args: tuple[str, ...] = Field(default=(), max_length=200)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    read_only: bool = Field(default=False, validation_alias=AliasChoices("read_only", "readOnly"))
    timeout: float = Field(default=120, gt=0, le=3600, allow_inf_nan=False)
    enabled: bool = True
    # 이 서버의 모든 도구 설명 뒤에 덧붙이는 사용 안내(모델이 선택 인수를 잘못 채울 때 쓴다)
    hint: str = Field(default="", max_length=2000)
    # 도구별로 모델에게 보이는 스키마에서 숨길 선택 인수. 키는 서버가 준 원래 도구 이름이다.
    # 모델이 모든 선택 인수를 채워 서버가 오해하는 경우(예: 빈 배열이 "아무것도 선택 안 함")에 쓴다.
    omit_params: dict[str, tuple[str, ...]] = Field(
        default_factory=dict, validation_alias=AliasChoices("omit_params", "omitParams")
    )


def parse_servers(
    raw: Mapping[str, Any], source: str
) -> tuple[dict[str, McpServerConfig], list[str]]:
    """`{"mcpServers": {...}}` 한 벌을 검증한다. 잘못된 항목은 건너뛰고 경고로 돌려준다."""
    entries = raw.get("mcpServers", raw.get("mcp_servers", {}))
    servers: dict[str, McpServerConfig] = {}
    warnings: list[str] = []
    if not isinstance(entries, dict):
        return servers, [f"{source}: mcpServers는 객체여야 합니다"]
    for name, entry in entries.items():
        if not isinstance(name, str) or not SERVER_NAME.fullmatch(name):
            warnings.append(f"{source}: 서버 이름 {name!r}은 영문·숫자·_- 32자 이하여야 합니다")
            continue
        if not isinstance(entry, dict):
            warnings.append(f"{source}: {name} 항목이 객체가 아닙니다")
            continue
        if "command" not in entry:
            kind = entry.get("type") or ("url" if "url" in entry else "알 수 없음")
            warnings.append(f"{source}: {name}은 stdio 서버가 아니어서({kind}) 건너뜁니다")
            continue
        try:
            config = McpServerConfig.model_validate(entry)
        except ValidationError as exc:
            warnings.append(f"{source}: {name} 설정이 올바르지 않습니다 ({exc.error_count()}건)")
            continue
        if config.enabled:
            servers[name] = config
    return servers, warnings


def load_servers_file(path: Path) -> tuple[dict[str, McpServerConfig], list[str]]:
    try:
        if not path.is_file():
            return {}, []
        if path.stat().st_size > MAX_CONFIG_BYTES:
            return {}, [f"{path}: 설정 파일이 너무 큽니다"]
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [f"{path}: 읽지 못했습니다 ({type(exc).__name__})"]
    if not isinstance(raw, dict):
        return {}, [f"{path}: 최상위가 객체여야 합니다"]
    return parse_servers(raw, str(path))


def servers_from_acp(items: Iterable[Any] | None) -> dict[str, McpServerConfig]:
    """ACP `mcpServers` 중 stdio 항목만 변환한다. HTTP/SSE는 아직 지원하지 않는다."""
    servers: dict[str, McpServerConfig] = {}
    for item in items or ():
        name = getattr(item, "name", None)
        command = getattr(item, "command", None)
        if not isinstance(name, str) or not isinstance(command, str):
            logger.warning("ACP mcpServers: stdio가 아닌 항목은 건너뜁니다")
            continue
        if not SERVER_NAME.fullmatch(name):
            logger.warning("ACP mcpServers: 서버 이름 %r이 올바르지 않아 건너뜁니다", name)
            continue
        env = {str(v.name): str(v.value) for v in getattr(item, "env", None) or ()}
        try:
            servers[name] = McpServerConfig(
                command=command, args=tuple(getattr(item, "args", None) or ()), env=env
            )
        except ValidationError:
            logger.warning("ACP mcpServers: %s 설정이 올바르지 않아 건너뜁니다", name)
    return servers


def resolve_servers(
    cwd: Path,
    *,
    trust_project: bool,
    client: Mapping[str, McpServerConfig] | None = None,
    global_path: Path = DEFAULT_GLOBAL_PATH,
) -> tuple[dict[str, McpServerConfig], list[str]]:
    """전역 < 프로젝트(신뢰 시) < ACP 인수 순으로 병합한다."""
    merged, warnings = load_servers_file(global_path)
    project_file = cwd / ".mcp.json"
    if trust_project:
        project, project_warnings = load_servers_file(project_file)
        merged.update(project)
        warnings.extend(project_warnings)
    elif project_file.is_file():
        warnings.append(
            f"{project_file}: 프로젝트를 신뢰하지 않아 MCP 서버를 실행하지 않습니다"
        )
    merged.update(client or {})
    return merged, warnings
