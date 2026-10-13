import asyncio
from dataclasses import dataclass
from typing import TypeVar, assert_never

from pydantic import BaseModel

from perdix.context.messages import TextBlock, UserMessage
from perdix.context.view import ModelView
from perdix.llm.base import (
    ModelRequest,
    Provider,
    Stop,
    TextDelta,
    ThinkingDelta,
    ToolUseDelta,
    ToolUseEnd,
    ToolUseStart,
    Usage,
)
from perdix.memory.redaction import redact
from perdix.memory.schema import MemoryConfig

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class MemoryGenerationError(Exception):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    instruction: str
    input_text: str


class MemoryGenerator:
    def __init__(self, config: MemoryConfig) -> None:
        self.config = config

    async def generate(self, provider: Provider, request: GenerationRequest, schema: type[T]) -> T:
        req = ModelRequest(
            system=request.instruction + "\nJSON만 반환하세요.\n"
            + "出力スキーマ: " + str(schema.model_json_schema()),
            view=ModelView(messages=[UserMessage(content=[TextBlock(text=request.input_text)])]),
            tools=[], max_tokens=self.config.max_tokens,
        )
        text = ""
        finished = False
        async with asyncio.timeout(self.config.timeout_seconds):
            async for event in provider.stream(req):
                match event:
                    case TextDelta(text=chunk):
                        text += chunk
                        if len(text) > 128_000:
                            raise MemoryGenerationError("memory output exceeds limit")
                    case Stop(reason=reason, message=message):
                        if reason != "end_turn":
                            raise MemoryGenerationError("memory generation did not finish normally")
                        if message is not None and message.tool_uses():
                            raise MemoryGenerationError("memory generation requested tools")
                        finished = True
                    case ToolUseStart() | ToolUseDelta() | ToolUseEnd():
                        raise MemoryGenerationError("memory generation requested tools")
                    case ThinkingDelta() | Usage():
                        continue
                    case unreachable:
                        assert_never(unreachable)
        if not finished:
            raise MemoryGenerationError("memory stream ended without stop")
        return schema.model_validate_json(redact(text))
