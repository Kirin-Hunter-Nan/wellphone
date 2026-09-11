from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from app.protocol import ChatRequest, ToolResultSubmission


class ProviderError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class ReplyStream(Protocol):
    def events(self) -> AsyncIterator[str]: ...


@dataclass(frozen=True, slots=True)
class ProviderToolCallContext:
    tool_call_id: str
    capability: str
    provider: str
    state: dict[str, object]


ToolCallContextSink = Callable[[ProviderToolCallContext], Awaitable[None]]


class ModelProvider(Protocol):
    async def open_reply(
        self,
        request: ChatRequest,
        *,
        on_tool_call: ToolCallContextSink | None = None,
    ) -> ReplyStream: ...
    async def continue_reply(
        self,
        context: ProviderToolCallContext,
        result: ToolResultSubmission,
    ) -> str: ...
    async def close(self) -> None: ...
