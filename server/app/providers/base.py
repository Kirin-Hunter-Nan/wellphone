from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from app.protocol import ChatRequest


class ProviderError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class ReplyStream(Protocol):
    def events(self) -> AsyncIterator[str]: ...


class ModelProvider(Protocol):
    async def open_reply(self, request: ChatRequest) -> ReplyStream: ...
    async def close(self) -> None: ...
