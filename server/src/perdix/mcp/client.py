"""MCP stdio 클라이언트(명세 2025-06-18, ADR 0034). tools만 지원한다."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from perdix.mcp.config import McpServerConfig
from perdix.sandbox.base import scrub_env

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = frozenset({"2025-06-18", "2025-03-26", "2024-11-05"})
MAX_LINE_BYTES = 16 * 1024 * 1024
MAX_STDERR_LINES = 200
MAX_TOOLS = 500
MAX_PAGES = 20
STARTUP_TIMEOUT = 30.0
LIST_TIMEOUT = 30.0
SHUTDOWN_GRACE = 2.0
CLIENT_NAME = "perdix"


class McpError(Exception):
    """연결·프로토콜·서버 오류. 도구 실행 오류(`isError`)와는 다르다."""


class McpCancelled(McpError):
    """사용자가 취소했다."""


def build_env(config: McpServerConfig, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """비밀 패턴을 제거한 환경 + 설정에 명시한 env(AGENTS.md 규칙 12)."""
    env = scrub_env(os.environ if base is None else base)
    env.update(config.env)
    return env


def resolve_command(command: str, env: Mapping[str, str]) -> str:
    if os.sep in command:
        return command
    return shutil.which(command, path=env.get("PATH")) or command


class McpClient:
    def __init__(
        self,
        name: str,
        config: McpServerConfig,
        cwd: Path,
        *,
        base_env: Mapping[str, str] | None = None,
    ) -> None:
        self.name = name
        self.config = config
        self.cwd = Path(config.cwd).expanduser() if config.cwd else cwd
        self._base_env = base_env
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()
        self._closing = False
        self.stderr_tail: list[str] = []
        self.server_info: dict[str, Any] = {}
        self.has_tools = False

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None and not self._closing

    async def start(self) -> None:
        env = build_env(self.config, self._base_env)
        argv = [resolve_command(self.config.command, env), *self.config.args]
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=env,
                start_new_session=True,
                limit=MAX_LINE_BYTES,
            )
        except (OSError, ValueError) as exc:
            raise McpError(f"{self.name}: 서버를 시작하지 못했습니다 ({exc})") from exc
        self._reader = asyncio.create_task(self._read_loop(), name=f"mcp-read-{self.name}")
        self._stderr_task = asyncio.create_task(self._drain_stderr(), name=f"mcp-err-{self.name}")
        try:
            result = await self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": CLIENT_NAME, "version": "0"},
                },
                timeout=STARTUP_TIMEOUT,
            )
            version = result.get("protocolVersion")
            if version not in SUPPORTED_VERSIONS:
                raise McpError(f"{self.name}: 지원하지 않는 프로토콜 버전입니다 ({version!r})")
            info = result.get("serverInfo")
            self.server_info = info if isinstance(info, dict) else {}
            capabilities = result.get("capabilities")
            self.has_tools = isinstance(capabilities, dict) and "tools" in capabilities
            await self._notify("notifications/initialized")
        except BaseException:
            await self.close()
            raise

    async def list_tools(self) -> list[dict[str, Any]]:
        if not self.has_tools:
            return []
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            result = await self._request("tools/list", params, timeout=LIST_TIMEOUT)
            page = result.get("tools")
            if not isinstance(page, list):
                raise McpError(f"{self.name}: tools/list 응답 형식이 올바르지 않습니다")
            tools.extend(item for item in page if isinstance(item, dict)
                         and isinstance(item.get("name"), str))
            cursor = result.get("nextCursor") if isinstance(result.get("nextCursor"), str) else None
            if not cursor or len(tools) >= MAX_TOOLS:
                break
        return tools[:MAX_TOOLS]

    async def call_tool(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        cancel: asyncio.Event | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "tools/call",
            {"name": tool, "arguments": arguments},
            timeout=timeout or self.config.timeout,
            cancel=cancel,
        )

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        proc = self._proc
        self._fail_pending(McpError(f"{self.name}: 연결을 닫았습니다"))
        if proc is not None:
            with contextlib.suppress(Exception):
                if proc.stdin is not None:
                    proc.stdin.close()
            if proc.returncode is None:
                await self._stop(proc)
        for task in (self._reader, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(t for t in (self._reader, self._stderr_task) if t is not None),
            return_exceptions=True,
        )

    # ---------------------------------------------------------------- 내부

    async def _stop(self, proc: asyncio.subprocess.Process) -> None:
        """stdin 닫기 → SIGTERM → SIGKILL(명세 권고)을 프로세스 그룹에 보낸다."""
        for sig in (None, signal.SIGTERM, signal.SIGKILL):
            if sig is not None:
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(proc.pid, sig)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), SHUTDOWN_GRACE)
            if proc.returncode is not None:
                return

    def _fail_pending(self, error: Exception) -> None:
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(error)

    async def _send(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            raise McpError(f"{self.name}: 서버가 실행 중이 아닙니다")
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        async with self._write_lock:
            try:
                proc.stdin.write(data)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise McpError(f"{self.name}: 서버에 쓸 수 없습니다") from exc

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        await self._send(message)

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
        cancel: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            )
            waiters: set[asyncio.Future[Any]] = {future}
            cancel_waiter: asyncio.Task[bool] | None = None
            if cancel is not None:
                cancel_waiter = asyncio.create_task(cancel.wait())
                waiters.add(cancel_waiter)
            try:
                done, _ = await asyncio.wait(
                    waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                if cancel_waiter is not None:
                    cancel_waiter.cancel()
            if future in done:
                return future.result()
            cancelled = cancel_waiter is not None and cancel_waiter in done
            reason = "cancelled" if cancelled else "timeout"
            await self._cancel_remote(request_id, reason)
            if reason == "cancelled":
                raise McpCancelled(f"{self.name}: {method} 요청을 취소했습니다")
            raise McpError(f"{self.name}: {method} 요청이 {timeout:g}초 안에 끝나지 않았습니다")
        finally:
            self._pending.pop(request_id, None)

    async def _cancel_remote(self, request_id: int, reason: str) -> None:
        with contextlib.suppress(McpError):
            await self._notify(
                "notifications/cancelled", {"requestId": request_id, "reason": reason}
            )

    async def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            while True:
                try:
                    line = await proc.stdout.readline()
                except ValueError:
                    self._fail_pending(McpError(f"{self.name}: 응답 한 줄이 너무 큽니다"))
                    await self._terminate_after_protocol_error(proc)
                    return
                if not line:
                    break
                await self._handle_line(line)
        finally:
            code = proc.returncode
            self._fail_pending(McpError(
                f"{self.name}: 서버가 종료되었습니다" + (f" (코드 {code})" if code else "")
                + (f": {self.stderr_tail[-1]}" if self.stderr_tail else "")
            ))

    async def _terminate_after_protocol_error(self, proc: asyncio.subprocess.Process) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(proc.pid, signal.SIGTERM)

    async def _handle_line(self, line: bytes) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            logger.debug("MCP %s: JSON이 아닌 출력 무시", self.name)
            return
        if not isinstance(message, dict):
            return
        message_id = message.get("id")
        method = message.get("method")
        if method is None and isinstance(message_id, int):
            future = self._pending.get(message_id)
            if future is None or future.done():
                return
            error = message.get("error")
            if isinstance(error, dict):
                future.set_exception(McpError(
                    f"{self.name}: 서버 오류 {error.get('code')}: {str(error.get('message'))[:500]}"
                ))
            else:
                result = message.get("result")
                future.set_result(result if isinstance(result, dict) else {})
            return
        if isinstance(method, str) and message_id is not None:
            # 서버가 보낸 요청: ping만 응답한다. sampling/roots/elicitation은 지원하지 않는다.
            reply: dict[str, Any] = {"jsonrpc": "2.0", "id": message_id}
            if method == "ping":
                reply["result"] = {}
            else:
                reply["error"] = {"code": -32601, "message": f"method not found: {method}"}
            with contextlib.suppress(McpError):
                await self._send(reply)

    async def _drain_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        while True:
            try:
                line = await proc.stderr.readline()
            except ValueError:
                continue
            if not line:
                return
            text = line.decode(errors="replace").rstrip()
            if not text:
                continue
            self.stderr_tail.append(text[:500])
            del self.stderr_tail[:-MAX_STDERR_LINES]
            logger.debug("MCP %s stderr: %s", self.name, text[:500])
