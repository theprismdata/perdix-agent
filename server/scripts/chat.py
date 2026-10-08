"""최소 터미널 ACP 클라이언트. TUI(M2)가 생기기 전까지 서버를 직접 써 보기 위한 도구.

사용:
    uv run python scripts/chat.py                 # Anthropic(ANTHROPIC_API_KEY 필요), build 모드
    uv run python scripts/chat.py --mode chat --model claude-sonnet-5-5
    uv run python scripts/chat.py --fake tests/fixtures/demo.json   # 네트워크 없이 데모
    uv run python scripts/chat.py -- --permission-mode accept-edits
    ('--' 뒤는 서버 인자 그대로 전달)

명령: /mode chat|plan|build, /cancel(실행 중 Ctrl+C), /quit
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from acp.helpers import text_block
from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    PermissionOption,
    RequestPermissionResponse,
    ToolCallUpdate,
)
from acp.stdio import spawn_agent_process

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
CYAN, YELLOW, GREEN, RED = "\033[36m", "\033[33m", "\033[32m", "\033[31m"


class TerminalClient:
    """session/update를 화면에 그리고 권한 요청은 stdin으로 묻는다."""

    def __init__(self) -> None:
        self.streaming_text = False
        self.titles: dict[str, str] = {}

    def _end_text(self) -> None:
        if self.streaming_text:
            print()
            self.streaming_text = False

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        kind = update.session_update
        if kind == "agent_message_chunk":
            print(update.content.text, end="", flush=True)
            self.streaming_text = True
        elif kind == "agent_thought_chunk":
            self._end_text()
            print(f"{DIM}💭 {update.content.text}{RESET}")
        elif kind == "tool_call":
            self._end_text()
            self.titles[update.tool_call_id] = update.title
            print(f"{CYAN}▶ {update.title}{RESET} {DIM}({update.kind}){RESET}")
        elif kind == "tool_call_update":
            title = self.titles.get(update.tool_call_id, update.tool_call_id)
            if update.status == "completed":
                self._end_text()
                print(f"{GREEN}✔ {title}{RESET}")
                for c in update.content or []:
                    if getattr(c, "type", None) == "diff":
                        print(f"{DIM}  diff {c.path}{RESET}")
                    elif getattr(c, "type", None) == "content":
                        text = getattr(c.content, "text", "")
                        for line in text.splitlines()[:12]:
                            print(f"{DIM}  {line}{RESET}")
                        if len(text.splitlines()) > 12:
                            print(f"{DIM}  … ({len(text.splitlines())} lines){RESET}")
            elif update.status == "failed":
                self._end_text()
                reason = ""
                for c in update.content or []:
                    if getattr(c, "type", None) == "content":
                        reason = getattr(c.content, "text", "")
                print(f"{RED}✘ {title}{RESET} {DIM}{reason[:200]}{RESET}")
        elif kind == "usage_update":
            self._end_text()
            print(f"{DIM}  context {update.used:,}/{update.size:,}{RESET}")
        elif kind == "current_mode_update":
            print(f"{DIM}  mode → {update.current_mode_id}{RESET}")
        elif kind == "user_message_chunk":
            print(f"{BOLD}you>{RESET} {update.content.text}")

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        self._end_text()
        print(f"{YELLOW}? 권한 요청: {tool_call.title}{RESET}")
        if tool_call.raw_input:
            print(f"{DIM}  {tool_call.raw_input}{RESET}")
        keys = {"y": "allow_once", "a": "allow_always", "n": "reject_once", "N": "reject_always"}
        while True:
            answer = await asyncio.to_thread(input, "  [y]허용 [a]항상 허용 [n]거절 [N]항상 거절: ")
            kind = keys.get(answer.strip())
            if kind:
                break
        for opt in options:
            if opt.kind == kind:
                return RequestPermissionResponse(
                    outcome=AllowedOutcome(outcome="selected", option_id=opt.option_id)
                )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--mode", default="build", choices=["chat", "plan", "build"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--fake", default=None, help="FakeProvider 스크립트 JSON 경로")
    ap.add_argument("--db", default=None)
    args, rest = ap.parse_known_args()
    if rest and rest[0] == "--":
        rest = rest[1:]
    return args, rest


async def main() -> int:
    args, server_extra = parse_args()
    server_args = ["-m", "perdix", "--log-level", "WARNING", *server_extra]
    if args.fake:
        server_args += ["--provider", "fake", "--fake-script", str(Path(args.fake).resolve())]
    elif not os.environ.get("ANTHROPIC_API_KEY") and not (
        Path.home() / ".perdix" / "credentials.json"
    ).is_file():
        print(f"{RED}API 키가 없습니다.{RESET} ANTHROPIC_API_KEY 또는 ~/.perdix/credentials.json")
        return 2
    if args.model:
        server_args += ["--model", args.model]
    if args.db:
        server_args += ["--db", args.db]

    client = TerminalClient()
    async with spawn_agent_process(
        client,
        sys.executable,
        *server_args,
        transport_kwargs={"stderr": None},
        use_unstable_protocol=True,
    ) as (conn, _process):
        init = await conn.initialize(protocol_version=1)
        version = init.agent_info.version if init.agent_info else ""
        print(f"{DIM}perdix-server {version} connected{RESET}")
        session = await conn.new_session(cwd=str(Path(args.cwd).resolve()), mcp_servers=[])
        sid = session.session_id
        await conn.set_session_mode(sid, args.mode)
        print(f"{DIM}session {sid[:8]}  cwd {args.cwd}  mode {args.mode}  (/quit 로 종료){RESET}")

        while True:
            try:
                line = await asyncio.to_thread(input, f"\n{BOLD}you>{RESET} ")
            except (EOFError, KeyboardInterrupt):
                break
            line = line.strip()
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            if line.startswith("/mode "):
                await conn.set_session_mode(sid, line.split(maxsplit=1)[1])
                continue
            task = asyncio.create_task(conn.prompt(sid, [text_block(line)]))
            try:
                resp = await task
            except KeyboardInterrupt:
                await conn.cancel(sid)
                resp = await task
            except Exception as exc:  # 서버가 돌려준 JSON-RPC 오류
                client._end_text()
                print(f"{RED}error: {exc}{RESET}")
                continue
            client._end_text()
            meta = (resp.field_meta or {}).get("perdix", {})
            extra = f"  verification={meta['verification']}" if meta.get("verification") else ""
            print(f"{DIM}[{resp.stop_reason}{extra}]{RESET}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
