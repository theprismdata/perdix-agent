"""테스트용 MCP stdio 서버. 명세 2025-06-18의 필요한 부분만 구현한다."""

from __future__ import annotations

import json
import os
import sys
import threading

LOG = os.environ.get("FAKE_MCP_LOG")
VERSION = os.environ.get("FAKE_MCP_VERSION", "2025-06-18")
NO_TOOLS = os.environ.get("FAKE_MCP_NO_TOOLS") == "1"

TOOLS = [
    {"name": "echo", "description": "입력 텍스트를 그대로 돌려준다",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                     "required": ["text"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True}},
    {"name": "fail", "description": "실행 오류", "inputSchema": {"type": "object"}},
    {"name": "slow", "description": "오래 걸림", "inputSchema": {"type": "object"}},
    {"name": "big", "description": "큰 출력", "inputSchema": {"type": "object"}},
    {"name": "env", "description": "환경 변수 이름", "inputSchema": {"type": "object"}},
    {"name": "rpcerror", "description": "프로토콜 오류", "inputSchema": {"type": "object"}},
    {"name": "image", "description": "이미지", "inputSchema": {"type": "object"}},
    {"name": "die", "description": "프로세스 종료", "inputSchema": {"type": "object"}},
    {"name": "weird/name.with chars", "description": "이름 정규화",
     "inputSchema": {"type": "object"}},
]


def log(entry: dict[str, object]) -> None:
    if LOG:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def send(message: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(request_id: object, result: dict[str, object]) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def call(name: str, arguments: dict[str, object]) -> dict[str, object] | None:
    if name == "echo":
        return {"content": [{"type": "text", "text": f"echo: {arguments.get('text')}"}]}
    if name == "fail":
        return {"content": [{"type": "text", "text": "의도한 실패"}], "isError": True}
    if name == "slow":
        return None
    if name == "big":
        return {"content": [{"type": "text", "text": "x" * 250_000}]}
    if name == "env":
        return {"content": [{"type": "text", "text": json.dumps(sorted(os.environ))}]}
    if name == "image":
        return {"content": [{"type": "image", "data": "AAAA", "mimeType": "image/png"}]}
    if name == "die":
        os._exit(3)
    return {"content": [{"type": "text", "text": f"called {name}"}]}


def main() -> None:
    sys.stderr.write("fake server starting\n")
    sys.stderr.flush()
    sys.stdout.write("not json banner\n")
    sys.stdout.flush()
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        request_id = message.get("id")
        log({"in": method, "raw": message})
        if method is None:
            continue
        if method == "initialize":
            capabilities: dict[str, object] = {} if NO_TOOLS else {"tools": {}}
            reply(request_id, {"protocolVersion": VERSION, "capabilities": capabilities,
                               "serverInfo": {"name": "fake", "version": "1"}})
            send({"jsonrpc": "2.0", "id": 900, "method": "ping"})
            send({"jsonrpc": "2.0", "id": 901, "method": "sampling/createMessage", "params": {}})
        elif method == "tools/list":
            cursor = (message.get("params") or {}).get("cursor")
            if cursor is None:
                reply(request_id, {"tools": TOOLS[:4], "nextCursor": "page2"})
            else:
                reply(request_id, {"tools": TOOLS[4:]})
        elif method == "tools/call":
            params = message["params"]
            name = params["name"]
            if name == "rpcerror":
                send({"jsonrpc": "2.0", "id": request_id,
                      "error": {"code": -32602, "message": "unknown tool: rpcerror"}})
                continue
            if name == "slow":
                # 입력 읽기를 막지 않도록 별도 스레드에서 늦게 응답한다(취소 알림 수신 확인용).
                threading.Timer(30, reply, (request_id, {"content": []})).start()
                continue
            result = call(name, params.get("arguments") or {})
            if result is not None:
                reply(request_id, result)
        elif request_id is not None and method not in ("ping",):
            send({"jsonrpc": "2.0", "id": request_id,
                  "error": {"code": -32601, "message": "method not found"}})
        elif method == "ping":
            reply(request_id, {})


if __name__ == "__main__":
    main()
