from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fixture_db import CUSTOMER_COUNT, ORDER_COUNT, PROMOTION_COUNT, build_commerce_db

from perdix.mcp.client import McpClient, McpError
from perdix.mcp.config import McpServerConfig
from perdix.ontology.pipeline import _run_query, extract_terms, run_dry_run

HERE = Path(__file__).parent
SERVER = str(HERE / "fixture_mcp_server.py")
DOCS = [HERE / "fixture_docs" / "order_policy.md", HERE / "fixture_docs" / "promotion_rules.md"]


def config(db_path: Path) -> McpServerConfig:
    return McpServerConfig(command=sys.executable, args=(SERVER,),
                           env={"ONTOLOGY_DB_PATH": str(db_path)})


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[McpClient]:
    db_path = tmp_path / "commerce.db"
    build_commerce_db(db_path)
    connection = McpClient("ontology", config(db_path), tmp_path)
    await connection.start()
    try:
        yield connection
    finally:
        await connection.close()


def test_extract_terms_finds_glossary_terms_in_docs() -> None:
    assert extract_terms(DOCS) == ["고객", "주문", "프로모션", "배송지"]


@pytest.mark.asyncio
async def test_dry_run_fills_evidence_from_real_queries(client: McpClient) -> None:
    result = await run_dry_run(client, DOCS)

    counts = {table.name: table.row_count for table in result.tables}
    assert counts == {"customers": CUSTOMER_COUNT, "orders": ORDER_COUNT,
                      "promotions": PROMOTION_COUNT}
    assert (CUSTOMER_COUNT, ORDER_COUNT, PROMOTION_COUNT) == (8, 20, 3)

    by_name = {candidate.name: candidate for candidate in result.candidates}
    assert set(by_name) == {"고객", "주문", "프로모션", "배송지"}
    for term, table, count in (("고객", "customers", 8), ("주문", "orders", 20),
                               ("프로모션", "promotions", 3)):
        candidate = by_name[term]
        assert candidate.confidence == "high"
        assert candidate.evidence_sql == f"SELECT COUNT(*) AS n FROM {table}"
        assert candidate.evidence_result == str(count)

    shipping = by_name["배송지"]
    assert shipping.confidence == "unverified"
    assert shipping.evidence_sql is None
    assert shipping.evidence_result is None


@pytest.mark.asyncio
async def test_query_tool_rejects_non_select(client: McpClient) -> None:
    result = await client.call_tool("run_query", {"sql": "  -- 주석\n DELETE FROM orders"})
    assert result.get("isError") is True
    with pytest.raises(McpError):
        await _run_query(client, "SELECT 1; DROP TABLE orders")
