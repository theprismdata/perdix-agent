import argparse
import logging
import os
from pathlib import Path

import uvicorn
from pydantic import SecretStr, ValidationError

from perdix.gateway.app import create_app
from perdix.gateway.config import GatewayConfig, GatewaySecrets


def main() -> None:
    parser = argparse.ArgumentParser(prog="perdix-gateway")
    parser.add_argument("--config", type=Path, required=True, help="게이트웨이 JSON 설정 파일")
    args = parser.parse_args()
    try:
        config = GatewayConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
        gateway_token = os.environ.get(config.token_env, "")
        if len(gateway_token) < 24:
            parser.error(f"{config.token_env} 환경 변수에 24자 이상의 연결 토큰을 설정하세요")
        telegram = os.environ.get(config.telegram.token_env, "") if config.telegram else ""
        if config.telegram and not telegram:
            parser.error("Telegram 토큰 환경 변수를 설정하세요")
        github = os.environ.get(config.github.token_env, "") if config.github else ""
        webhook = os.environ.get(config.github.webhook_secret_env, "") if config.github else ""
        secrets = GatewaySecrets(
            gateway=SecretStr(gateway_token),
            telegram=SecretStr(telegram) if telegram else None,
            github=SecretStr(github) if github else None,
            webhook=SecretStr(webhook) if webhook else None,
        )
    except (OSError, ValidationError):
        parser.error("설정 파일을 읽지 못했습니다. 파일 경로와 JSON 설정을 확인하세요")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # HTTP 클라이언트의 URL에는 Telegram 토큰이 포함되어 있어 요청 로그를 비활성화한다.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpcore2").setLevel(logging.WARNING)
    uvicorn.run(create_app(config, secrets), host=config.host, port=config.port, access_log=False)


if __name__ == "__main__":
    main()
