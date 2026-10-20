import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest
import pytest_asyncio
from pydantic import JsonValue, SecretStr

from perdix.config.schema import ServerConfig
from perdix.gateway.channels import GatewayChannels, TelegramUpdate
from perdix.gateway.config import GatewayConfig, GatewaySecrets, ProjectConfig, TelegramConfig
from perdix.gateway.events import GatewayStore
from perdix.gateway.runtime import GatewayRuntime
from perdix.memory.schema import MemoryConfig
from perdix.session.store import EventStore


def update(text: str, index: int, identity: tuple[int, int] = (123, 123)) -> TelegramUpdate:
    return TelegramUpdate.model_validate({"update_id": index, "message": {
        "chat": {"id": identity[0]}, "from": {"id": identity[1]}, "text": text,
    }})


def click(data: str, index: int, user: int = 123) -> TelegramUpdate:
    return TelegramUpdate.model_validate({"update_id": index, "callback_query": {
        "id": str(index), "from": {"id": user}, "data": data,
        "message": {"message_id": 50, "chat": {"id": 123}},
    }})


@pytest_asyncio.fixture
async def connected(tmp_path: Path) -> AsyncIterator[
    tuple[GatewayChannels, list[str], list[dict[str, JsonValue]]]
]:
    script = tmp_path / "replies.json"
    script.write_text('[{"text":"OK"}]')
    config = GatewayConfig(db_path=tmp_path / "gateway.db", projects={
        "perdix": ProjectConfig(cwd=tmp_path),
    }, server=ServerConfig(provider="fake", fake_script=script,
        db_path=str(tmp_path / "sessions.db"), credentials_path=tmp_path / "credentials.json",
        memories=MemoryConfig(enabled=False)), telegram=TelegramConfig(
            allowed_user_ids=[123, 456], allowed_chat_ids=[123, 456],
        ))
    runtime = GatewayRuntime(config, GatewayStore(config.db_path))
    sent = []
    requests = []

    def transport(request: httpx2.Request) -> httpx2.Response:
        payload = json.loads(request.content)
        sent.append(payload["text"])
        requests.append({"method": request.url.path.rsplit("/", 1)[-1], **payload})
        return httpx2.Response(200, json={"ok": True})

    await runtime.start()
    try:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as http:
            yield GatewayChannels(runtime, GatewaySecrets(
                gateway=SecretStr("test-gateway-token-long-enough"),
                telegram=SecretStr("test-bot"),
            ), http), sent, requests
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_search_and_pagination_do_not_submit_jobs(connected):
    # Given: Telegram 계정이 허용된 실제 Gateway 채널이다.
    channels, sent, requests = connected
    # When: 목록, 검색, 두 번째 페이지를 요청한다.
    for index, command in enumerate(("/model", "/model deepseek-v4.1", "/model page 2")):
        await channels.handle_telegram(update(command, index))
    # Then: 검색 결과는 제한 길이로 전달되며 모델 호출은 발생하지 않는다.
    buttons = requests[1]["reply_markup"]["inline_keyboard"]
    assert any(button["text"] == "deepseek/deepseek-v4.1-flash"
               for row in buttons for button in row)
    assert sent[0] != sent[2]
    assert all(len(text) < 4096 for text in sent)
    assert await channels.runtime.store.jobs() == []


@pytest.mark.asyncio
async def test_model_selection_is_scoped_persistent_and_idempotent(connected):
    # Given: 서로 다른 두 사용자와 채팅에서 모델 선택 요청을 보낸다.
    channels, _, _ = connected
    await channels.handle_telegram(update("/model deepseek/deepseek-v4.1-flash", 1))
    await channels.handle_telegram(update("/model moonshotai/kimi-k3", 2, (456, 456)))
    # When: 이미 처리한 요청과 잘못된 ID, 허용되지 않은 사용자의 요청을 재전달한다.
    await channels.handle_telegram(update("/model deepseek/deepseek-v4.1-flash", 1))
    await channels.handle_telegram(update("/model no-such-model", 3))
    await channels.handle_telegram(update("/model qwen/qwen3.8-flash", 4, (123, 999)))
    restored = GatewayStore(channels.runtime.config.db_path)
    await restored.open()
    try:
        # Then: 선택은 영속 기록으로 복구되고 사용자·채팅 사이에 섞이지 않는다.
        first = "telegram:123:123:perdix:perdix"
        second = "telegram:456:456:perdix:perdix"
        assert await restored.selected_model(first) == "deepseek/deepseek-v4.1-flash"
        assert await restored.selected_model(second) == "moonshotai/kimi-k3"
        assert len(await restored.events(first)) == 1
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_changed_model_is_applied_to_reused_acp_session(connected):
    # Given: 모델을 선택하고 기존 대화에서 한 차례 응답을 받았다.
    channels, _, _ = connected
    runtime = channels.runtime
    await channels.handle_telegram(update("/model deepseek/deepseek-v4.1-flash", 1))
    await channels.handle_telegram(update("hello", 2))
    first = (await runtime.store.jobs())[0]
    async with asyncio.timeout(20):
        await runtime.tasks[first.id]
    # When: 모델을 바꾼 뒤 후속 질문을 보낸다.
    await channels.handle_telegram(update("/model moonshotai/kimi-k3", 3))
    await channels.handle_telegram(update("followup", 4))
    second = (await runtime.store.jobs())[0]
    async with asyncio.timeout(20):
        await runtime.tasks[second.id]
    first = await runtime.store.get(first.id)
    second = await runtime.store.get(second.id)
    # Then: 같은 ACP 세션에 표준 모델 선택이 적용되고 작업의 모델 스냅샷은 보존된다.
    assert first is not None and second is not None
    assert first.status == second.status == "completed"
    assert second.result == "OK"
    assert first.session_id == second.session_id
    assert first.model == "deepseek/deepseek-v4.1-flash"
    assert second.model == "moonshotai/kimi-k3"
    store = await EventStore.open(runtime.config.server.db_path)
    try:
        record = await store.get_session(second.session_id)
        assert record.model == "moonshotai/kimi-k3"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_buttons_select_model_without_typing_id(connected):
    # Given: 사용자가 모델 메뉴를 열었다.
    channels, _, requests = connected
    await channels.handle_telegram(update("/model", 1))
    families = requests[-1]["reply_markup"]["inline_keyboard"]
    deepseek = next(b for row in families for b in row if b["text"] == "DeepSeek")
    # When: 모델 종류와 실제 모델을 버튼으로 선택한다.
    await channels.handle_telegram(click(deepseek["callback_data"], 2))
    next_page = next(b for row in requests[-1]["reply_markup"]["inline_keyboard"] for b in row
                     if b["text"] == "다음 페이지")
    await channels.handle_telegram(click(next_page["callback_data"], 3))
    page = requests[-1]
    model = next(b for row in page["reply_markup"]["inline_keyboard"] for b in row
                 if b["text"] == "deepseek/deepseek-v4.1-flash")
    await channels.handle_telegram(click(model["callback_data"], 4))
    # Then: 같은 메시지를 갱신하고 선택을 저장하며 버튼 로딩을 종료한다.
    assert page["method"] == "editMessageText" and page["message_id"] == 50
    assert await channels.runtime.store.selected_model(
        "telegram:123:123:perdix:perdix",
    ) == "deepseek/deepseek-v4.1-flash"
    assert requests[-1]["reply_markup"]["inline_keyboard"] == []
    acknowledgements = [r for r in requests if r["method"] == "answerCallbackQuery"]
    assert len(acknowledgements) == 3
    assert all(not r["show_alert"] for r in acknowledgements)
    assert all(len(b["callback_data"].encode()) <= 64
               for row in page["reply_markup"]["inline_keyboard"] for b in row)


@pytest.mark.asyncio
async def test_button_navigation_and_owner_validation(connected):
    # Given: 전체 목록 첫 페이지의 버튼이 있다.
    channels, _, requests = connected
    await channels.handle_telegram(update("/model page 1", 1))
    first = requests[-1]["reply_markup"]["inline_keyboard"]
    next_page = next(b for row in first for b in row if b["text"] == "다음 페이지")
    # When: 다른 사용자가 누른 뒤 소유자가 다음 페이지로 이동한다.
    await channels.handle_telegram(click(next_page["callback_data"], 2, user=456))
    assert requests[-1]["method"] == "answerCallbackQuery"
    assert requests[-1]["show_alert"]
    await channels.handle_telegram(click(next_page["callback_data"], 3))
    second = requests[-1]["reply_markup"]["inline_keyboard"]
    # Then: 소유자만 탐색하고 페이지마다 다른 모델 버튼을 받는다.
    assert requests[-1]["method"] == "editMessageText"
    assert {row[0]["text"] for row in first[:10]}.isdisjoint(
        {row[0]["text"] for row in second[:10]},
    )
    assert await channels.runtime.store.selected_model("telegram:123:123:perdix:perdix") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["model:missing:0", "model:missing:-1", "other:data"])
async def test_invalid_callbacks_never_change_model(connected, data: str):
    # Given / When: 존재하지 않거나 변조된 버튼이 전달된다.
    channels, _, requests = connected
    await channels.handle_telegram(click(data, 1))
    # Then: 경고만 응답하며 메시지나 모델을 변경하지 않는다.
    assert len(requests) == 1 and requests[0]["method"] == "answerCallbackQuery"
    assert requests[0]["show_alert"]
    assert await channels.runtime.store.selected_model("telegram:123:123:perdix:perdix") is None
