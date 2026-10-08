"""세션 히스토리와 ModelView가 공유하는 메시지 모델.

공급자 중립 형식이다. 와이어 형식 변환은 `llm/<provider>.py` 어댑터 책임이다.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- 사용자 입력 블록


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(BaseModel):
    """base64로 인코딩된 이미지."""

    type: Literal["image"] = "image"
    media_type: str
    data: str


ContentBlock = Annotated[TextBlock | ImageBlock, Field(discriminator="type")]

# ---------------------------------------------------------------- 사용자 표시용 콘텐츠


class TextDisplay(BaseModel):
    type: Literal["text"] = "text"
    text: str


class DiffDisplay(BaseModel):
    """파일 변경 표시. 새 파일이면 `old_text`는 None.

    `path`는 resolve된 절대 경로다(ACP Diff.path 계약). 모델용 텍스트는 상대 경로를 쓴다.
    """

    type: Literal["diff"] = "diff"
    path: str
    old_text: str | None = None
    new_text: str


DisplayContent = Annotated[TextDisplay | DiffDisplay, Field(discriminator="type")]

# ---------------------------------------------------------------- assistant 아이템


class TextItem(BaseModel):
    kind: Literal["text"] = "text"
    text: str


class ToolUseItem(BaseModel):
    kind: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class OpaqueItem(BaseModel):
    """thinking/reasoning/signature 같은 공급자 불투명 블록. `raw`는 원본 그대로 보관한다."""

    kind: Literal["thinking", "reasoning", "signature"]
    provider: str
    model: str
    raw: dict[str, Any]


AssistantItem = Annotated[TextItem | ToolUseItem | OpaqueItem, Field(discriminator="kind")]

# ---------------------------------------------------------------- 메시지


class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: list[ContentBlock]
    origin: Literal["user", "synthetic"] = "user"

    @classmethod
    def from_text(cls, text: str, origin: Literal["user", "synthetic"] = "user") -> UserMessage:
        return cls(content=[TextBlock(text=text)], origin=origin)

    def text(self) -> str:
        """텍스트 블록만 이어 붙인 문자열."""
        return "\n".join(b.text for b in self.content if isinstance(b, TextBlock))


class AssistantMessage(BaseModel):
    """정상 종료한 스트림 하나. `items`는 등장 순서 그대로다."""

    role: Literal["assistant"] = "assistant"
    items: list[AssistantItem]
    provider: str
    model: str
    stop_reason: str

    def tool_uses(self) -> list[ToolUseItem]:
        return [i for i in self.items if isinstance(i, ToolUseItem)]

    def text(self) -> str:
        return "\n".join(i.text for i in self.items if isinstance(i, TextItem))


class ToolResult(BaseModel):
    """도구 결과 하나. `index`는 해당 assistant 메시지 안 tool_use 등장 순서(0부터)."""

    tool_use_id: str
    index: int
    content: str
    is_error: bool = False
    display: list[DisplayContent] = Field(default_factory=list)


class ToolResultsGroup(BaseModel):
    """한 assistant 메시지의 tool_use들에 대한 결과 묶음(`index` 순). 투영 전용, 저장하지 않는다."""

    kind: Literal["tool_results"] = "tool_results"
    results: list[ToolResult]
