import time
from dataclasses import dataclass
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from perdix.gateway.runtime import GatewayRuntime
from perdix.llm import catalog


@dataclass(frozen=True, slots=True)
class ModelCommand:
    conversation: str
    arguments: str
    update_id: int


class ModelButton(BaseModel):
    text: str
    callback_data: str


class ModelReply(BaseModel):
    text: str
    inline_keyboard: list[list[ModelButton]] = []
    alert: bool = False


class ModelMenu(BaseModel):
    model_config = ConfigDict(frozen=True)
    conversation: str
    project: str
    actions: list[str]
    created_at: float


async def menu_reply(runtime: GatewayRuntime, menu: ModelMenu, labels: list[str]) -> ModelReply:
    token = uuid4().hex
    await runtime.store.append("modelMenu:" + token, "telegramModelMenu",
                               menu.model_dump(mode="json"))
    current = await runtime.store.selected_model(
        f"{menu.conversation}:{menu.project}:perdix",
    ) or runtime.config.server.model
    return ModelReply(
        text=f"프로젝트: {menu.project}\n현재 모델: {current}\n선택할 버튼을 눌러주세요.",
        inline_keyboard=[[ModelButton(text=label, callback_data=f"model:{token}:{index}")]
                         for index, label in enumerate(labels)])


async def model_reply(runtime: GatewayRuntime, project: str, command: ModelCommand) -> ModelReply:
    if runtime.config.projects[project].acp:
        return ModelReply(text="이 프로젝트는 외부 에이전트입니다. "
                          "모델은 해당 에이전트에서 설정하세요.")
    key = f"{command.conversation}:{project}:perdix"
    current = await runtime.store.selected_model(key) or runtime.config.server.model
    models = {model.id: model for model in catalog.all_models()}
    argument = command.arguments.strip()
    if argument in models:
        source = f"telegramModel:{command.conversation}:{command.update_id}"
        async with runtime.submit_lock:
            if not await runtime.store.source_job(source):
                await runtime.store.append(key, "telegramModel", argument, source)
            current = await runtime.store.selected_model(key) or current
        return ModelReply(text=f"모델: {current}\n다음 요청부터 적용됩니다. "
                          "이전 대화는 유지됩니다.")
    if not argument:
        families = [("DeepSeek", "deepseek/"), ("Kimi", "moonshotai/"),
                    ("GLM", "z-ai/"), ("Qwen", "qwen/"), ("MiniMax", "minimax/"),
                    ("Claude", "anthropic/"), ("GPT", "openai/"), ("Gemini", "google/"),
                    ("전체 모델", "page 1")]
        return await menu_reply(runtime, ModelMenu(conversation=command.conversation,
            project=project, actions=[query for _, query in families], created_at=time.time()),
            [name for name, _ in families])
    page = 1
    query = argument
    if argument.startswith("page "):
        number, _, query = argument[5:].partition(" ")
        try:
            page = int(number)
        except ValueError:
            return ModelReply(text="사용법: /model page 2")
    matches = sorted(model for model in models if query.casefold() in model.casefold())
    pages = max(1, (len(matches) + 9) // 10)
    if page < 1 or page > pages:
        return ModelReply(text=f"페이지는 1~{pages} 사이로 입력하세요. 모델은 변경되지 않았습니다.")
    if not matches:
        return ModelReply(text=f"일치하는 모델이 없습니다. 현재 모델: {current}")
    entries = matches[(page - 1) * 10:page * 10]
    actions = list(entries)
    labels = list(entries)
    if page > 1:
        actions.append(f"page {page - 1} {query}")
        labels.append("이전 페이지")
    if page < pages:
        actions.append(f"page {page + 1} {query}")
        labels.append("다음 페이지")
    actions.append("")
    labels.append("모델 종류로 돌아가기")
    reply = await menu_reply(runtime, ModelMenu(conversation=command.conversation,
        project=project, actions=actions, created_at=time.time()), labels)
    return reply.model_copy(update={
        "text": reply.text + f"\n모델 목록 {page}/{pages} ({len(matches)}개)",
    })


async def callback_reply(runtime: GatewayRuntime, command: ModelCommand) -> ModelReply:
    parts = command.arguments.split(":")
    if len(parts) != 3 or parts[0] != "model":
        return ModelReply(text="버튼을 확인할 수 없습니다. /model로 다시 열어주세요.", alert=True)
    events = await runtime.store.events("modelMenu:" + parts[1])
    if not events:
        return ModelReply(text="메뉴가 만료되었습니다. /model로 다시 열어주세요.", alert=True)
    menu = ModelMenu.model_validate(events[0].data)
    if menu.conversation != command.conversation:
        return ModelReply(text="본인이 연 모델 메뉴만 사용할 수 있습니다.", alert=True)
    if time.time() - menu.created_at > 3600 or menu.project not in runtime.config.projects:
        return ModelReply(text="메뉴가 만료되었습니다. /model로 다시 열어주세요.", alert=True)
    try:
        index = int(parts[2])
    except ValueError:
        return ModelReply(text="버튼을 확인할 수 없습니다. /model로 다시 열어주세요.", alert=True)
    if index < 0 or index >= len(menu.actions):
        return ModelReply(text="버튼을 확인할 수 없습니다. /model로 다시 열어주세요.", alert=True)
    return await model_reply(runtime, menu.project,
        ModelCommand(command.conversation, menu.actions[index], command.update_id))
