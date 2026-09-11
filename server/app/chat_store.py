from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Literal, Protocol
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from app.protocol import ChatMessage, ChatRequest


class ChatRequestConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ChatRequestClaim:
    status: Literal["claimed", "processing", "completed", "conflict"]
    events: tuple[str, ...] = ()


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


class PostgreSQLChatRequestStore:
    def __init__(self, database_url: str) -> None:
        self._pool = AsyncConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=5,
            open=False,
            check=AsyncConnectionPool.check_connection,
        )

    async def initialize(self) -> None:
        await self._pool.open(wait=True, timeout=30)
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_requests (
                    conversation_id UUID NOT NULL,
                    request_id TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    lease_expires_at TIMESTAMPTZ,
                    response_events_json TEXT,
                    last_error_code TEXT,
                    created_at TIMESTAMPTZ NOT NULL,
                    completed_at TIMESTAMPTZ,
                    PRIMARY KEY (conversation_id, request_id)
                )
                """
            )
            await connection.execute(
                "ALTER TABLE chat_requests ADD COLUMN IF NOT EXISTS request_json TEXT"
            )
            await connection.execute(
                "ALTER TABLE chat_requests ADD COLUMN IF NOT EXISTS assistant_content TEXT"
            )
            await connection.execute(
                """
                CREATE INDEX IF NOT EXISTS chat_requests_conversation_created_idx
                ON chat_requests (conversation_id, created_at)
                """
            )

    async def is_healthy(self) -> bool:
        try:
            async with self._pool.connection(timeout=2) as connection:
                cursor = await connection.execute("SELECT 1")
                return await cursor.fetchone() == (1,)
        except Exception:
            return False

    async def claim(
        self,
        conversation_id: UUID,
        request: ChatRequest,
        *,
        lease_seconds: int,
    ) -> ChatRequestClaim:
        fingerprint = _request_fingerprint(request)
        now = datetime.now(timezone.utc)
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO chat_requests (
                    conversation_id,
                    request_id,
                    request_fingerprint,
                    status,
                    lease_expires_at,
                    created_at,
                    request_json
                ) VALUES (%s, %s, %s, 'processing', %s, %s, %s)
                ON CONFLICT (conversation_id, request_id) DO NOTHING
                RETURNING request_id
                """,
                (
                    conversation_id,
                    request.request_id,
                    fingerprint,
                    lease_expires_at,
                    now,
                    request.model_dump_json(by_alias=True),
                ),
            )
            if await cursor.fetchone() is not None:
                return ChatRequestClaim(status="claimed")

            retry_cursor = await connection.execute(
                """
                UPDATE chat_requests
                SET status = 'processing',
                    attempts = attempts + 1,
                    lease_expires_at = %s,
                    last_error_code = NULL,
                    request_json = COALESCE(request_json, %s)
                WHERE conversation_id = %s
                  AND request_id = %s
                  AND request_fingerprint = %s
                  AND (
                    status = 'failed'
                    OR (
                      status = 'processing'
                      AND (lease_expires_at IS NULL OR lease_expires_at <= %s)
                    )
                  )
                RETURNING request_id
                """,
                (
                    lease_expires_at,
                    request.model_dump_json(by_alias=True),
                    conversation_id,
                    request.request_id,
                    fingerprint,
                    now,
                ),
            )
            if await retry_cursor.fetchone() is not None:
                return ChatRequestClaim(status="claimed")

            existing_cursor = await connection.execute(
                """
                SELECT request_fingerprint, status, response_events_json
                FROM chat_requests
                WHERE conversation_id = %s AND request_id = %s
                """,
                (conversation_id, request.request_id),
            )
            existing = await existing_cursor.fetchone()

        if existing is None or existing[0] != fingerprint:
            return ChatRequestClaim(status="conflict")
        if existing[1] == "completed" and existing[2] is not None:
            return ChatRequestClaim(
                status="completed",
                events=tuple(json.loads(existing[2])),
            )
        return ChatRequestClaim(status="processing")

    async def complete(
        self,
        conversation_id: UUID,
        request: ChatRequest,
        events: tuple[str, ...],
        assistant_content: str | None,
    ) -> None:
        fingerprint = _request_fingerprint(request)
        events_json = json.dumps(events, ensure_ascii=False, separators=(",", ":"))
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE chat_requests
                SET status = 'completed',
                    lease_expires_at = NULL,
                    response_events_json = %s,
                    assistant_content = %s,
                    last_error_code = NULL,
                    completed_at = %s
                WHERE conversation_id = %s
                  AND request_id = %s
                  AND request_fingerprint = %s
                  AND status = 'processing'
                RETURNING request_id
                """,
                (
                    events_json,
                    assistant_content,
                    datetime.now(timezone.utc),
                    conversation_id,
                    request.request_id,
                    fingerprint,
                ),
            )
            if await cursor.fetchone() is not None:
                return
            existing_cursor = await connection.execute(
                """
                SELECT request_fingerprint, status, response_events_json
                FROM chat_requests
                WHERE conversation_id = %s AND request_id = %s
                """,
                (conversation_id, request.request_id),
            )
            existing = await existing_cursor.fetchone()
        if existing != (fingerprint, "completed", events_json):
            raise ChatRequestConflictError(
                "A different response already exists for this chat request"
            )

    async def contextualize(
        self,
        conversation_id: UUID,
        request: ChatRequest,
        *,
        max_messages: int,
        max_characters: int,
    ) -> ChatRequest:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT request_json, assistant_content
                FROM chat_requests
                WHERE conversation_id = %s
                  AND request_json IS NOT NULL
                  AND created_at <= COALESCE(
                    (
                      SELECT created_at
                      FROM chat_requests
                      WHERE conversation_id = %s AND request_id = %s
                    ),
                    created_at
                  )
                ORDER BY created_at, request_id
                """,
                (conversation_id, conversation_id, request.request_id),
            )
            rows = await cursor.fetchall()
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
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                UPDATE chat_requests
                SET assistant_content = %s
                WHERE conversation_id = %s AND request_id = %s
                """,
                (assistant_content, conversation_id, request_id),
            )

    async def fail(
        self,
        conversation_id: UUID,
        request_id: str,
        error_code: str,
    ) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                UPDATE chat_requests
                SET status = 'failed',
                    lease_expires_at = NULL,
                    last_error_code = %s
                WHERE conversation_id = %s
                  AND request_id = %s
                  AND status = 'processing'
                """,
                (error_code, conversation_id, request_id),
            )

    async def close(self) -> None:
        await self._pool.close()


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


def _request_fingerprint(request: ChatRequest) -> str:
    payload = request.model_dump(
        mode="json",
        by_alias=True,
        exclude={"request_id"},
    )
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _context_messages(
    rows: list[tuple[object, object]],
    fallback: ChatRequest,
) -> list[ChatMessage]:
    requests: list[tuple[ChatRequest, str | None]] = []
    for request_json, assistant_content in rows:
        if not isinstance(request_json, str):
            continue
        try:
            stored_request = ChatRequest.model_validate_json(request_json)
        except ValueError:
            continue
        requests.append((
            stored_request,
            assistant_content if isinstance(assistant_content, str) else None,
        ))
    if not requests:
        return list(fallback.messages)

    messages = list(requests[0][0].messages)
    if requests[0][1]:
        messages.append(ChatMessage(role="assistant", content=requests[0][1]))
    for stored_request, assistant_content in requests[1:]:
        messages.append(stored_request.messages[-1])
        if assistant_content:
            messages.append(ChatMessage(role="assistant", content=assistant_content))
    return messages


def _trim_messages(
    messages: list[ChatMessage],
    *,
    max_messages: int,
    max_characters: int,
) -> list[ChatMessage]:
    selected: list[ChatMessage] = []
    character_count = 0
    for message in reversed(messages):
        content = message.model_dump(mode="json")["content"]
        size = len(
            content
            if isinstance(content, str)
            else json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        )
        if selected and (
            len(selected) >= max_messages
            or character_count + size > max_characters
        ):
            break
        selected.append(message)
        character_count += size
    return list(reversed(selected))
