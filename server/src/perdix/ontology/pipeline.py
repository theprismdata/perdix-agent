"""온톨로지 구축 드라이런 파이프라인: 수집 → 추출 → 후보 생성 → 검증.

원칙 1: 숫자와 관계는 코드가 추측하지 않고 MCP로 실제 실행한 쿼리 결과에서만 온다.
원칙 2: 근거 쿼리가 없는 후보는 미확인(unverified)으로 남긴다.
에이전트·도구 레지스트리에 연결되지 않은 시제품이다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from perdix.mcp.client import McpClient, McpError
from perdix.ontology.models import CandidateEntity, DryRunResult, TableInfo

QUERY_TOOL = "run_query"
LIST_TABLES_SQL = "SELECT name FROM sqlite_master WHERE type='table'"

# 휴리스틱: 고정 용어집과 문서 본문을 단순 문자열 매칭한다. NLP/LLM 추출이 아니다.
GLOSSARY: tuple[str, ...] = ("고객", "주문", "프로모션", "배송지")

# 용어 → 테이블 명시 매핑. 일반 퍼지 매칭은 하지 않는다. 매핑이 없는 용어는 근거가 없다.
TERM_TABLES: dict[str, str] = {
    "고객": "customers",
    "주문": "orders",
    "프로모션": "promotions",
}


async def _run_query(client: McpClient, sql: str) -> list[dict[str, Any]]:
    """run_query 도구를 호출하고 텍스트 결과를 JSON 행 목록으로 해석한다."""
    result = await client.call_tool(QUERY_TOOL, {"sql": sql})
    texts = [item.get("text", "") for item in result.get("content", [])
             if isinstance(item, dict) and item.get("type") == "text"]
    text = "".join(texts)
    if result.get("isError"):
        raise McpError(f"{QUERY_TOOL} 실패: {text}")
    rows = json.loads(text)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise McpError(f"{QUERY_TOOL} 응답이 행 목록이 아닙니다: {text[:200]}")
    return rows


def _count_sql(table: str) -> str:
    return f"SELECT COUNT(*) AS n FROM {table}"


async def _count_rows(client: McpClient, table: str) -> int:
    rows = await _run_query(client, _count_sql(table))
    if len(rows) != 1 or not isinstance(rows[0].get("n"), int):
        raise McpError(f"COUNT 결과 형식이 올바르지 않습니다: {rows!r}")
    count: int = rows[0]["n"]
    return count


async def collect_schema(client: McpClient) -> list[TableInfo]:
    """테이블 목록과 행 수를 쿼리로 수집한다."""
    rows = await _run_query(client, LIST_TABLES_SQL)
    names = [row["name"] for row in rows if isinstance(row.get("name"), str)]
    tables: list[TableInfo] = []
    for name in names:
        # 방금 조회한 목록에서 온 이름 중 ASCII 식별자 문자만 쓰는 것만 SQL에 넣는다.
        if not name.isascii() or not name.replace("_", "").isalnum():
            continue
        tables.append(TableInfo(name=name, row_count=await _count_rows(client, name)))
    return tables


def extract_terms(doc_paths: Sequence[Path]) -> list[str]:
    """문서에서 용어집 용어를 찾는다. 결과는 용어집 순서이며 중복이 없다."""
    text = "\n".join(path.read_text(encoding="utf-8") for path in doc_paths)
    return [term for term in GLOSSARY if term in text]


def generate_candidates(tables: list[TableInfo], terms: list[str]) -> list[CandidateEntity]:
    """용어마다 후보를 만든다. 아직 쿼리를 돌리지 않았으므로 근거는 비우고 unverified로 둔다."""
    return [
        CandidateEntity(name=term, kind="entity", evidence_sql=None,
                        evidence_result=None, confidence="unverified")
        for term in terms
    ]


async def validate_candidates(
    client: McpClient,
    candidates: list[CandidateEntity],
    tables: list[TableInfo],
) -> list[CandidateEntity]:
    """매핑된 테이블이 있는 후보만 근거 쿼리를 실제로 실행해 채운다. 나머지는 unverified."""
    row_counts = {table.name: table.row_count for table in tables}
    validated: list[CandidateEntity] = []
    for candidate in candidates:
        table = TERM_TABLES.get(candidate.name)
        if table is None or table not in row_counts:
            validated.append(candidate.model_copy(update={
                "evidence_sql": None, "evidence_result": None, "confidence": "unverified",
            }))
            continue
        sql = _count_sql(table)
        count = await _count_rows(client, table)
        validated.append(candidate.model_copy(update={
            "evidence_sql": sql,
            "evidence_result": str(count),
            "confidence": "high" if count == row_counts[table] else "medium",
        }))
    return validated


async def run_dry_run(client: McpClient, doc_paths: Sequence[Path]) -> DryRunResult:
    """이미 시작된 클라이언트로 수집 → 추출 → 후보 생성 → 검증을 차례로 돌린다."""
    tables = await collect_schema(client)
    terms = extract_terms(doc_paths)
    candidates = generate_candidates(tables, terms)
    validated = await validate_candidates(client, candidates, tables)
    return DryRunResult(tables=tables, candidates=validated)
