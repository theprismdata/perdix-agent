# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.9"]
# ///
# 실행: uv run workflow_tests/fixtures/external_acp.py [load|fresh] [이름]
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, JsonValue


class Request(BaseModel):
    id: int | str | None = None
    method: str
    params: dict[str, JsonValue] = {}


def emit(payload: dict[str, JsonValue]) -> None:
    print(json.dumps(payload), flush=True)


def main() -> None:
    supports_load = sys.argv[1] == "load"
    label = sys.argv[2]
    for line in sys.stdin:
        request = Request.model_validate_json(line)
        if request.id is None:
            continue
        result: dict[str, JsonValue] = {}
        match request.method:
            case "initialize":
                result = {"protocolVersion": 1, "agentCapabilities": {"loadSession": supports_load}}
            case "session/new":
                result = {"sessionId": str(uuid4())}
            case "session/load":
                if not supports_load:
                    raise RuntimeError("session/load is unsupported")
            case "session/prompt":
                session = str(request.params["sessionId"])
                state = Path(session + ".json")
                count = int(state.read_text()) + 1 if state.exists() else 1
                state.write_text(str(count))
                text = json.dumps({
                    "agent": label, "session": session, "count": count,
                    "secrets_present": any(k in os.environ for k in (
                        "PERDIX_GATEWAY_TOKEN", "PERDIX_TELEGRAM_TOKEN", "CUSTOM_BOT_SECRET",
                    )),
                })
                emit({"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": session, "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": text},
                        "_meta": {"perdix": {"workflow": {"runId": "external-run"}}},
                    },
                }})
                result = {"stopReason": "end_turn"}
            case _:
                emit({"jsonrpc": "2.0", "id": request.id,
                      "error": {"code": -32601, "message": "unsupported method"}})
                continue
        emit({"jsonrpc": "2.0", "id": request.id, "result": result})


if __name__ == "__main__":
    main()
