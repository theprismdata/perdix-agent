"""`perdix-server` 진입점: argv → ServerConfig → ACP over stdio.

stdout은 ACP 전용이다. 로그와 오류 메시지는 모두 stderr로 보낸다.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
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
    parser.add_argument(
        "--provider", choices=["anthropic", "openai", "gemini", "openrouter", "fake"],
    )
    parser.add_argument("--model")
    parser.add_argument("--team")
    parser.add_argument("--member")
    parser.add_argument("--team-role", choices=["leader", "member"])
    parser.add_argument("--team-worker", action="store_true")
    parser.add_argument("--team-db")
    parser.add_argument("--team-project")
    parser.add_argument("--openai-auth", choices=["login", "status", "logout"])
    parser.add_argument("--effort")
    parser.add_argument("--langflow-config", help="Langflow 연동 설정 JSON 파일(선택 기능)")
    parser.add_argument("--memory-config", help="자동 메모리 설정 JSON 파일")
    parser.add_argument(
        "--permission-mode",
        dest="permission_mode",
        choices=["interactive", "deny", "accept-edits", "allowlist"],
    )
    parser.add_argument("--max-tokens", dest="max_tokens", type=int)
    parser.add_argument(
        "--trust-project", action="store_true", help="기동 cwd의 프로젝트 설정 신뢰"
    )
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
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    graceful_team_shutdown = (
        config.team is not None and sys.platform != "win32" and task is not None
    )
    if graceful_team_shutdown and task is not None:
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        await acp.run_agent(agent, use_unstable_protocol=True)
    finally:
        await agent.aclose()
        if graceful_team_shutdown:
            loop.remove_signal_handler(signal.SIGTERM)


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if args.openai_auth is not None:
        from pathlib import Path

        from perdix.llm.base import ProviderError
        from perdix.llm.openai_auth import run_auth

        try:
            path = Path(args.credentials_path) if args.credentials_path else None
            print(asyncio.run(run_auth(args.openai_auth, path)))
        except (ProviderError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        return
    del args.openai_auth
    memory_path = args.memory_config
    del args.memory_config
    langflow_path = args.langflow_config
    del args.langflow_config
    values: dict[str, Any] = {k: v for k, v in vars(args).items() if v is not None}
    if langflow_path is not None:
        from pathlib import Path

        from perdix.integrations.langflow.config import LangflowConfig

        try:
            values["langflow"] = LangflowConfig.model_validate_json(
                Path(langflow_path).read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            parser.error(f"Langflow 설정 파일을 읽지 못했습니다: {exc}")
    if memory_path is not None:
        from pathlib import Path

        from perdix.memory.schema import MemoryConfig

        try:
            values["memories"] = MemoryConfig.model_validate_json(
                Path(memory_path).read_text(encoding="utf-8")
            )
        except (OSError, ValidationError):
            parser.error("메모리 설정 파일을 읽지 못했습니다")
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
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    main()
