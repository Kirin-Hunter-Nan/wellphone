"""In-memory conversation request store used by tests and local composition."""

from __future__ import annotations

from uuid import UUID

from app.api.protocol import ChatRequest
from app.conversations.context import (
    _context_messages,
    _request_fingerprint,
    _trim_messages,
)
from app.conversations.models import ChatRequestClaim, ChatRequestConflictError

class InMemoryChatRequestStore:
    def __init__(self) -> None:
        self._requests: dict[tuple[UUID, str], dict[str, object]] = {}

    async def initialize(self) -> None:
        pass

    async def is_healthy(self) -> bool:
        return True

    async def claim(
        self,
        conversation_id: UUID,
        request: ChatRequest,
        *,
        lease_seconds: int,
    ) -> ChatRequestClaim:
        del lease_seconds
        key = (conversation_id, request.request_id)
        fingerprint = _request_fingerprint(request)
        existing = self._requests.get(key)
        if existing is None:
            self._requests[key] = {
                "fingerprint": fingerprint,
                "status": "processing",
                "events": (),
                "request": request,
                "assistant_content": None,
            }
            return ChatRequestClaim(status="claimed")
        if existing["fingerprint"] != fingerprint:
            return ChatRequestClaim(status="conflict")
        if existing["status"] == "completed":
            return ChatRequestClaim(
                status="completed",
                events=existing["events"],  # type: ignore[arg-type]
            )
        if existing["status"] == "failed":
            existing["status"] = "processing"
            existing["request"] = request
            return ChatRequestClaim(status="claimed")
        return ChatRequestClaim(status="processing")

    async def complete(
        self,
        conversation_id: UUID,
        request: ChatRequest,
        events: tuple[str, ...],
        assistant_content: str | None,
    ) -> None:
        key = (conversation_id, request.request_id)
        existing = self._requests.get(key)
        fingerprint = _request_fingerprint(request)
        if existing is None or existing["fingerprint"] != fingerprint:
            raise ChatRequestConflictError("Chat request is unavailable")
        if existing["status"] == "completed" and existing["events"] != events:
            raise ChatRequestConflictError(
                "A different response already exists for this chat request"
            )
        existing["status"] = "completed"
        existing["events"] = events
        existing["assistant_content"] = assistant_content

    async def contextualize(
        self,
        conversation_id: UUID,
        request: ChatRequest,
        *,
        max_messages: int,
        max_characters: int,
    ) -> ChatRequest:
        rows = [
            (
                stored["request"].model_dump_json(by_alias=True),  # type: ignore[union-attr]
                stored["assistant_content"],
            )
            for (stored_conversation_id, _), stored in self._requests.items()
            if stored_conversation_id == conversation_id
        ]
        messages = _context_messages(rows, request)
        return request.model_copy(update={
            "messages": _trim_messages(
                messages,
                max_messages=max_messages,
                max_characters=max_characters,
            )
        })

    async def save_assistant_reply(
        self,
        conversation_id: UUID,
        request_id: str,
        assistant_content: str,
    ) -> None:
        existing = self._requests.get((conversation_id, request_id))
        if existing is not None:
            existing["assistant_content"] = assistant_content

    async def fail(
        self,
        conversation_id: UUID,
        request_id: str,
        error_code: str,
    ) -> None:
        del error_code
        existing = self._requests.get((conversation_id, request_id))
        if existing is not None and existing["status"] == "processing":
            existing["status"] = "failed"

    async def close(self) -> None:
        pass
