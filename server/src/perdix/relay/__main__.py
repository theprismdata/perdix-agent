"""`perdix-relay`: Perdix 인증으로 OpenAI 호환 LLM 엔드포인트를 연다(loopback 전용)."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from perdix.config.credentials import DEFAULT_CREDENTIALS_PATH
from perdix.relay.app import DEFAULT_TOKEN_PATH, create_app, load_or_create_token


def main() -> None:
    parser = argparse.ArgumentParser(prog="perdix-relay")
    parser.add_argument("--port", type=int, default=18796)
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_PATH)
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS_PATH)
    parser.add_argument(
        "--workspace", type=Path,
        help="지정하면 이 디렉터리에 한정한 읽기 전용 내장 도구를 /mcp로 연다",
    )
    parser.add_argument(
        "--permission-mode", dest="permission_mode", default="deny",
        choices=["deny", "accept-edits", "allowlist"],
        help="/mcp 도구 호출에 적용할 Perdix 권한 모드. 승인 채널이 없어 ask는 거부된다",
    )
    args = parser.parse_args()
    token = load_or_create_token(args.token_file)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = create_app(token, credentials_path=args.credentials, workspace=args.workspace,
                     permission_mode=args.permission_mode)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
