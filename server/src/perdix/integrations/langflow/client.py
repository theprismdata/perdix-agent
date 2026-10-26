"""Langflow 공개 HTTP 계약(`/api/v1/version`, `/api/v1/run/{flow}`)만 쓰는 얇은 클라이언트.

Langflow 내부 모듈이나 langflow-sdk에 의존하지 않는다(ADR 0026). 성공·실패 판단은 HTTP 상태뿐
아니라 본문의 오류 표시까지 보고, 모든 실패를 LangflowError 하나로 정규화한다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx2

from perdix.integrations.langflow.config import LangflowConfig, parse_version
from perdix.integrations.langflow.errors import LangflowError

Clock = Callable[[], float]
MAX_RESPONSE_BYTES = 4_194_304


@dataclass(frozen=True, slots=True)
class FlowResult:
    text: str
    session_id: str | None
    truncated: bool = False


def _detail_message(body: Any) -> str:
    """`detail`이 문자열, JSON 문자열, 객체, 422 배열인 경우를 한 줄 메시지로 바꾼다."""
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, str):
        raw = detail.strip()
        try:
            detail = json.loads(raw)
        except ValueError:
            return raw
        if isinstance(detail, str):
            return raw
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("error") or detail)[:2000].strip()
    if isinstance(detail, list):
        return "; ".join(
            f"{'.'.join(str(p) for p in item.get('loc', ()))}: {item.get('msg')}"
            if isinstance(item, dict) else str(item)
            for item in detail
        )[:2000]
    return str(detail)[:2000]


def _json_or_text(response: httpx2.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:2000]


def _collect_texts(payload: Any) -> tuple[list[str], str | None]:
    """v1 응답의 outputs[].outputs[].results.message에서 텍스트와 오류 표시를 모은다."""
    texts: list[str] = []
    error: str | None = None
    for group in payload.get("outputs") or []:
        for output in group.get("outputs") or []:
            results = output.get("results")
            message = results.get("message") if isinstance(results, dict) else None
            if not isinstance(message, dict):
                continue
            inner = message.get("data")
            data: dict[str, Any] = inner if isinstance(inner, dict) else message
            text = data.get("text") or message.get("text") or ""
            if data.get("error"):
                error = error or str(text or "플로우가 오류 메시지를 반환했습니다")
            elif text:
                texts.append(str(text))
    return texts, error


class LangflowClient:
    def __init__(
        self,
        config: LangflowConfig,
        transport: httpx2.AsyncBaseTransport | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        self.config = config
        self._key = config.read_api_key()
        self._transport = transport
        self._clock = clock
        self._failures = 0
        self._open_until = 0.0
        self._verified: str | None = None

    def _client(self) -> httpx2.AsyncClient:
        headers = {"User-Agent": "Perdix/1.0 (langflow adapter)", "Accept": "application/json"}
        if self._key:
            headers["x-api-key"] = self._key
        return httpx2.AsyncClient(
            base_url=self.config.base_url, headers=headers, transport=self._transport,
            trust_env=False, follow_redirects=False,
            timeout=httpx2.Timeout(self.config.timeout_seconds, connect=5),
        )

    def _check_breaker(self) -> None:
        if self._failures >= self.config.breaker_failures and self._clock() < self._open_until:
            raise LangflowError("unavailable", "연속 연결 실패로 잠시 호출을 멈췄습니다")

    def _record(self, ok: bool) -> None:
        if ok:
            self._failures = 0
            return
        self._failures += 1
        self._verified = None
        if self._failures >= self.config.breaker_failures:
            self._open_until = self._clock() + self.config.breaker_seconds

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx2.Response:
        self._check_breaker()
        try:
            async with self._client() as client:
                response = await client.request(method, path, **kwargs)
        except httpx2.TimeoutException as exc:
            self._record(False)
            seconds = f"{self.config.timeout_seconds:g}"
            raise LangflowError("timeout", f"{seconds}초 안에 응답이 없습니다") from exc
        except httpx2.TransportError as exc:
            self._record(False)
            reason = type(exc).__name__
            raise LangflowError("unavailable", f"Langflow에 연결하지 못했습니다: {reason}") from exc
        self._record(True)
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise LangflowError("bad_response", "응답이 너무 큽니다", response.status_code)
        return response

    async def verify(self) -> str:
        """접속한 곳이 지원 범위의 Langflow인지 확인하고 버전을 돌려준다(성공은 캐시)."""
        if self._verified is not None:
            return self._verified
        response = await self._request("GET", "/api/v1/version")
        body = _json_or_text(response)
        if (
            response.status_code != 200
            or not isinstance(body, dict)
            or body.get("package") != "Langflow"
        ):
            raise LangflowError(
                "wrong_instance",
                "응답이 Langflow의 /api/v1/version 형식이 아닙니다",
                response.status_code,
            )
        try:
            version = parse_version(str(body.get("version")))
        except ValueError as exc:
            raise LangflowError("wrong_instance", str(exc)) from exc
        low, high = parse_version(self.config.min_version), parse_version(self.config.max_version)
        if not low <= version < high:
            raise LangflowError(
                "unsupported_version",
                f"Langflow {body.get('version')}은 지원 범위({self.config.min_version} 이상, "
                f"{self.config.max_version} 미만) 밖입니다",
            )
        self._verified = str(body["version"])
        return self._verified

    async def run(self, alias: str, input_value: str, session_id: str | None = None) -> FlowResult:
        flow_id = self.config.flows.get(alias)
        if flow_id is None:
            raise LangflowError("unknown_flow", f"허용 목록에 없는 플로우입니다: {alias}")
        if not input_value.strip() or len(input_value) > self.config.max_input_chars:
            raise LangflowError(
                "invalid_input", f"입력은 1~{self.config.max_input_chars}자여야 합니다"
            )
        await self.verify()
        body: dict[str, Any] = {
            "input_value": input_value, "input_type": "chat", "output_type": "chat",
        }
        if session_id:
            body["session_id"] = session_id
        response = await self._request("POST", f"/api/v1/run/{flow_id}", json=body)
        return self._result(response)

    def _result(self, response: httpx2.Response) -> FlowResult:
        status, body = response.status_code, _json_or_text(response)
        if status == 200 and isinstance(body, dict):
            texts, error = _collect_texts(body)
            if error is not None:
                raise LangflowError("execution", error[:2000], status)
            text = "\n".join(texts)
            limit = self.config.max_output_chars
            session = body.get("session_id")
            return FlowResult(
                text=text[:limit], session_id=session if isinstance(session, str) else None,
                truncated=len(text) > limit,
            )
        message = _detail_message(body)
        if status in {401, 403}:
            raise LangflowError("auth", message, status)
        if status == 404:
            raise LangflowError("flow_not_found", message, status)
        if status in {400, 422}:
            raise LangflowError("invalid_input", message, status)
        if status == 200:
            raise LangflowError("bad_response", "JSON 객체가 아닌 응답입니다", status)
        raise LangflowError("execution" if status == 500 else "bad_response", message, status)
