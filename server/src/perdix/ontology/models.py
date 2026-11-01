"""온톨로지 드라이런 결과 모델."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class TableInfo(BaseModel):
    model_config = ConfigDict(frozen=True, alias_generator=to_camel, populate_by_name=True)
    name: str
    row_count: int


class CandidateEntity(BaseModel):
    """후보 엔티티/관계. 근거 쿼리를 실제로 실행하기 전에는 confidence가 unverified다."""

    model_config = ConfigDict(frozen=True, alias_generator=to_camel, populate_by_name=True)
    name: str
    kind: Literal["entity", "relation"]
    evidence_sql: str | None
    evidence_result: str | None
    confidence: Literal["high", "medium", "low", "unverified"]


class DryRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, alias_generator=to_camel, populate_by_name=True)
    tables: list[TableInfo]
    candidates: list[CandidateEntity]
