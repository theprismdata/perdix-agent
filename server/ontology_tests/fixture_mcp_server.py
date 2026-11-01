"""온톨로지 드라이런용 MCP stdio 서버. run_query(SELECT 전용) 도구 하나만 제공한다.

JSON-RPC 처리는 mcp_tests/fake_mcp_server.py와 같은 줄 단위 방식을 따른다.
DB 경로는 ONTOLOGY_DB_PATH 환경 변수로 받는다.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys

DB_PATH = os.environ.get("ONTOLOGY_DB_PATH", "")
VERSION = "2025-06-18"

TOOLS = [
    {"name": "run_query", "description": "읽기 전용 SELECT 문을 실행해 행 목록을 JSON으로 준다",
     "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}},
                     "required": ["sql"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True}},
]

_COMMENTS = re.compile(r"(--[^\n]*)|(/\*.*?\*/)", re.DOTALL)


def send(message: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(request_id: object, result: dict[str, object]) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def tool_error(text: str) -> dict[str, object]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def is_select(sql: str) -> bool:
    # 주석과 공백을 걷어낸 뒤 첫 단어가 SELECT이고 문장이 하나뿐인지 본다.
    stripped = _COMMENTS.sub(" ", sql).strip().rstrip(";").strip()
    return stripped.lower().startswith("select") and ";" not in stripped


def run_query(arguments: dict[str, object]) -> dict[str, object]:
    sql = arguments.get("sql")
    if not isinstance(sql, str):
        return tool_error("sql 인자는 문자열이어야 합니다")
    if not is_select(sql):
        return tool_error("SELECT 문만 허용됩니다")
    if not DB_PATH:
        return tool_error("ONTOLOGY_DB_PATH가 설정되지 않았습니다")
    # 읽기 전용 URI로 열어 쓰기를 이중으로 막는다.
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in connection.execute(sql).fetchall()]
    except sqlite3.Error as exc:
        return tool_error(f"쿼리 실패: {exc}")
    finally:
        connection.close()
    return {"content": [{"type": "text", "text": json.dumps(rows, ensure_ascii=False)}]}


def main() -> None:
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        request_id = message.get("id")
        if method is None:
            continue
        if method == "initialize":
            reply(request_id, {"protocolVersion": VERSION, "capabilities": {"tools": {}},
                               "serverInfo": {"name": "ontology-fixture", "version": "1"}})
        elif method == "tools/list":
            reply(request_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = message["params"]
            name = params["name"]
            if name != "run_query":
                reply(request_id, tool_error(f"알 수 없는 도구: {name}"))
                continue
            reply(request_id, run_query(params.get("arguments") or {}))
        elif request_id is not None and method not in ("ping",):
            send({"jsonrpc": "2.0", "id": request_id,
                  "error": {"code": -32601, "message": "method not found"}})
        elif method == "ping":
            reply(request_id, {})


if __name__ == "__main__":
    main()
