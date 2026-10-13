from __future__ import annotations

from pathlib import Path
from typing import Literal

from perdix.config.credentials import load_credentials
from perdix.llm.openai_credentials import oauth_path, read_record
from perdix.llm.openai_oauth import OpenAiOAuth


async def run_auth(
    action: Literal["login", "status", "logout"], credentials_path: Path | None,
) -> str:
    key = load_credentials(credentials_path).api_key("openai")
    path = oauth_path(credentials_path)
    oauth = OpenAiOAuth(path)
    match action:
        case "login":
            if key:
                return "openai: API 키 사용 중 (ChatGPT 로그인 생략)"
            await oauth.login()
            return "openai: ChatGPT 로그인 완료"
        case "status":
            if key:
                return "openai: API 키 사용 중 (환경변수 또는 credentials.json)"
            record = read_record(path)
            if record and record.refresh_token:
                return "openai: ChatGPT 로그인 저장됨 (만료 시 자동 갱신)"
            return "openai: 로그인되어 있지 않음 (perdix auth login --provider openai)"
        case "logout":
            confirmed = await oauth.logout()
            return (
                "openai: ChatGPT 로그아웃 완료 (API 키 설정은 유지)" if confirmed
                else "openai: 로컬 로그아웃 완료. 원격 해제는 확인하지 못했습니다. "
                "ChatGPT 설정에서 Perdix 연결을 해제해 주세요."
            )
