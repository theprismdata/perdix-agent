"""Langflow 플로우가 루프를 소유하는 Turn 실행기 (ADR 0027).

`TurnRunner.run`과 같은 시그니처를 가지며 한 Turn을 `POST /api/v1/run/{flow}?stream=true`
한 번으로 위임한다. Langflow 이벤트(token / add_message / end / error)를 하네스 이벤트로 바꾼다.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import ssl
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx2
import truststore

from perdix.context.messages import (
    AssistantMessage,
    ContentBlock,
    TextBlock,
    TextItem,
    UserMessage,
)
from perdix.harness.events import (
    TextDelta,
    ToolFailed,
    ToolFinished,
    ToolStarted,
    TurnEnded,
)
from perdix.harness.turn import AskPermission, Sink, TurnResult
from perdix.llm.base import ProviderError
from perdix.session.store import EventStore
from perdix.tools.base import TextDisplay, ToolOutput

PROVIDER_NAME = "langflow"


def prompt_text(prompt: list[ContentBlock]) -> str:
    """플로우 입력은 텍스트뿐이다. 이미지 블록은 무시한다."""
    return "\n".join(b.text for b in prompt if isinstance(b, TextBlock))


async def _lines(response: httpx2.Response) -> AsyncIterator[dict[str, Any]]:
    """Langflow는 이벤트마다 JSON 한 줄을 보낸다(빈 줄로 구분)."""
    async for line in response.aiter_lines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _parse_input(raw: Any) -> dict[str, Any]:
    """`log` 이벤트의 input_str은 파이썬 repr 문자열이다. 딕셔너리로 못 바꾸면 통째로 감싼다."""
    if isinstance(raw, dict):
        return raw
    try:
        value = ast.literal_eval(str(raw))
    except (ValueError, SyntaxError):
        return {"input": raw}
    return value if isinstance(value, dict) else {"input": value}


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


class LangflowTurnRunner:
    def __init__(
        self,
        *,
        store: EventStore,
        base_url: str,
        flow: str,
        api_key: str | None,
        model_label: str = "langflow",
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout: float = 600.0,
    ) -> None:
        self.store = store
        self.url = f"{base_url.rstrip('/')}/api/v1/run/{flow}"
        self.api_key = api_key
        self.model_label = model_label
        self._transport = transport
        self._timeout = timeout

    def _client(self) -> httpx2.AsyncClient:
        if self._transport is not None:
            return httpx2.AsyncClient(transport=self._transport, timeout=self._timeout)
        return httpx2.AsyncClient(
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            timeout=self._timeout,
            trust_env=False,
        )

    async def run(
        self,
        session_id: str,
        prompt: list[ContentBlock],
        *,
        cancel: asyncio.Event,
        sink: Sink,
        ask_permission: AskPermission,
        origin: Literal["user", "synthetic"] = "user",
    ) -> TurnResult:
        del ask_permission  # 플로우 안의 도구는 Langflow가 실행한다(ADR 0027).
        await self.store.append(
            session_id, "user_message", UserMessage(content=prompt, origin=origin)
        )
        result = await self._race_cancel(session_id, prompt_text(prompt), cancel, sink)
        await sink(TurnEnded(stop_reason=result.stop_reason))
        return result

    async def _race_cancel(
        self, session_id: str, text: str, cancel: asyncio.Event, sink: Sink
    ) -> TurnResult:
        stream = asyncio.create_task(self._stream(session_id, text, sink))
        waiter = asyncio.create_task(cancel.wait())
        try:
            await asyncio.wait({stream, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if not stream.done():
                stream.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stream
                return TurnResult(stop_reason="cancelled")
            return stream.result()
        finally:
            waiter.cancel()

    async def _stream(self, session_id: str, text: str, sink: Sink) -> TurnResult:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        body = {
            "input_value": text, "input_type": "chat", "output_type": "chat",
            "session_id": session_id,
        }
        streamed: list[str] = []
        final_text = ""
        seen_tools: dict[str, bool] = {}  # 도구 키 → 완료 여부
        log_tools = False  # `log` 이벤트로 도구가 보고되면 content_blocks 쪽은 중복이라 건너뛴다
        try:
            async with self._client() as client, client.stream(
                "POST", self.url, params={"stream": "true"}, json=body, headers=headers
            ) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode("utf-8", "replace")[:500]
                    raise ProviderError(
                        f"langflow {response.status_code}: {detail}",
                        retryable=response.status_code >= 500, status=response.status_code,
                    )
                async for event in _lines(response):
                    kind = event.get("event")
                    data = event.get("data") or {}
                    if kind == "token":
                        chunk = _stringify(data.get("chunk"))
                        if chunk:
                            streamed.append(chunk)
                            await sink(TextDelta(text=chunk))
                    elif kind == "log":
                        log_tools = await self._log_tool(data, seen_tools, sink) or log_tools
                    elif kind == "add_message":
                        if data.get("sender") == "User":
                            continue
                        if not log_tools:
                            await self._emit_tools(data, seen_tools, sink)
                        final_text = _stringify(data.get("text")) or final_text
                    elif kind == "error":
                        raise ProviderError(
                            f"langflow error: {_stringify(data.get('error') or data)}",
                            retryable=False,
                        )
                    elif kind == "end":
                        break
        except httpx2.HTTPError as exc:
            raise ProviderError(f"langflow 연결 실패: {exc}", retryable=True) from exc
        answer = "".join(streamed)
        if not answer and final_text:
            answer = final_text
            await sink(TextDelta(text=final_text))
        if answer:
            await self.store.append(session_id, "assistant_message", AssistantMessage(
                items=[TextItem(text=answer)], provider=PROVIDER_NAME,
                model=self.model_label, stop_reason="end_turn",
            ))
        return TurnResult(stop_reason="end_turn")

    async def _log_tool(self, data: dict[str, Any], seen: dict[str, bool], sink: Sink) -> bool:
        """`Tool Start`/`Tool End`/`Tool Error` 로그를 도구 이벤트로 바꾼다. 처리했으면 True."""
        message = data.get("message") or {}
        kind = message.get("type")
        if kind not in ("tool_start", "tool_end", "tool_error"):
            return False
        key = f"lf-{message.get('run_id') or message.get('parent_run_id') or len(seen)}"
        if kind == "tool_start":
            serialized = message.get("serialized") or {}
            name = str(serialized.get("name") or "tool")
            seen[key] = False
            await sink(ToolStarted(
                tool_use_id=key, name=name, kind="other", title=name,
                input=_parse_input(message.get("input_str")),
            ))
        elif key in seen and not seen[key]:
            seen[key] = True
            if kind == "tool_error":
                await sink(ToolFailed(tool_use_id=key, reason=_stringify(message.get("error"))))
            else:
                output = message.get("output")
                text = _stringify(output.get("content") if isinstance(output, dict) else output)
                await sink(ToolFinished(
                    tool_use_id=key,
                    output=ToolOutput(
                        content=text, is_error=bool(isinstance(output, dict)
                                                    and output.get("is_error")),
                        display=[TextDisplay(text=text)],
                    ),
                ))
        return True

    async def _emit_tools(
        self, message: dict[str, Any], seen: dict[str, bool], sink: Sink
    ) -> None:
        """`content_blocks[].contents[]`의 tool_use를 도구 이벤트로 바꾼다(add_message는 반복됨)."""
        for b, block in enumerate(message.get("content_blocks") or []):
            for c, content in enumerate(block.get("contents") or []):
                if content.get("type") != "tool_use":
                    continue
                name = str(content.get("name") or "tool")
                key = f"lf-{message.get('id') or message.get('timestamp', '')}-{b}-{c}-{name}"
                tool_input = content.get("tool_input") or content.get("input") or {}
                if key not in seen:
                    seen[key] = False
                    await sink(ToolStarted(
                        tool_use_id=key, name=name, kind="other", title=name,
                        input=tool_input if isinstance(tool_input, dict) else {"input": tool_input},
                    ))
                if seen[key]:
                    continue
                if content.get("error"):
                    seen[key] = True
                    await sink(ToolFailed(tool_use_id=key, reason=_stringify(content["error"])))
                elif content.get("output") is not None:
                    seen[key] = True
                    await sink(ToolFinished(tool_use_id=key, output=ToolOutput(
                        content=_stringify(content["output"]),
                        display=[TextDisplay(text=_stringify(content["output"]))],
                    )))
