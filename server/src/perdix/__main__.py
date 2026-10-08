"""`perdix-server` 진입점: argv → ServerConfig → ACP over stdio.

stdout은 ACP 전용이다. 로그와 오류 메시지는 모두 stderr로 보낸다.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

import acp
from pydantic import ValidationError

from perdix import __version__
from perdix.config.schema import ServerConfig


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="perdix-server", description="Perdix agent server (ACP over stdio)"
    )
    parser.add_argument("--version", action="version", version=f"perdix-server {__version__}")
    parser.add_argument("--db", dest="db_path", help="SQLite event log path")
    parser.add_argument("--provider", choices=["anthropic", "openai", "fake"])
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument(
        "--permission-mode",
        dest="permission_mode",
        choices=["interactive", "deny", "accept-edits", "allowlist"],
    )
    parser.add_argument("--max-tokens", dest="max_tokens", type=int)
    parser.add_argument("--fake-script", dest="fake_script", help="FakeProvider JSON script")
    parser.add_argument(
        "--agents-config",
        dest="agents_config_path",
        help="역할별 모델 오버라이드 JSON 파일 경로 (architecture.md §6.10)",
    )
    parser.add_argument(
        "--credentials",
        dest="credentials_path",
        help="API key file (default ~/.perdix/credentials.json)",
    )
    parser.add_argument(
        "--log-level",
        dest="log_level",
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    return parser


async def _serve(config: ServerConfig) -> None:
    from perdix.acp.agent import PerdixAgent

    agent = PerdixAgent(config)
    try:
        await acp.run_agent(agent, use_unstable_protocol=True)
    finally:
        await agent.aclose()


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    values: dict[str, Any] = {k: v for k, v in vars(args).items() if v is not None}
    try:
        config = ServerConfig(**values)
    except ValidationError as exc:
        messages = "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())
        parser.error(messages)
    if config.fake_script is not None and not config.fake_script.is_file():
        parser.error(f"fake script not found: {config.fake_script}")
    if config.agents_config_path is not None and not config.agents_config_path.is_file():
        parser.error(f"agents config not found: {config.agents_config_path}")

    logging.basicConfig(
        stream=sys.stderr,
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_serve(config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
