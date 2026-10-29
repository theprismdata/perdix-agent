"""Langflow 응답을 하나의 오류 형식으로 정규화한 예외."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ErrorCode = Literal[
    "disabled",
    "unknown_flow",
    "invalid_input",
    "unavailable",
    "timeout",
    "auth",
    "flow_not_found",
    "execution",
    "unsupported_version",
    "wrong_instance",
    "bad_response",
]
# 같은 입력으로 다시 시도해 볼 만한 코드
RETRYABLE: frozenset[ErrorCode] = frozenset({"unavailable", "timeout"})


@dataclass(frozen=True, slots=True)
class LangflowError(Exception):
    code: ErrorCode
    message: str
    status: int | None = None

    def __str__(self) -> str:
        return f"Langflow {self.code}: {self.message}"

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE
