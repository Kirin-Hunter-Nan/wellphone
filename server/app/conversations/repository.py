"""Persistence contract for idempotent conversation requests."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.api.protocol import ChatRequest
from app.conversations.models import ChatRequestClaim

class ChatRequestStore(Protocol):
    async def initialize(self) -> None: ...
    async def is_healthy(self) -> bool: ...
    async def claim(
        self,
        conversation_id: UUID,
        request: ChatRequest,
        *,
        lease_seconds: int,
    ) -> ChatRequestClaim: ...
    async def complete(
        self,
        conversation_id: UUID,
        request: ChatRequest,
        events: tuple[str, ...],
        assistant_content: str | None,
    ) -> None: ...
    async def contextualize(
        self,
        conversation_id: UUID,
        request: ChatRequest,
        *,
        max_messages: int,
        max_characters: int,
    ) -> ChatRequest: ...
    async def save_assistant_reply(
        self,
        conversation_id: UUID,
        request_id: str,
        assistant_content: str,
    ) -> None: ...
    async def fail(
        self,
        conversation_id: UUID,
        request_id: str,
        error_code: str,
    ) -> None: ...
    async def close(self) -> None: ...
