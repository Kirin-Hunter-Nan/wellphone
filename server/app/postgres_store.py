from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Protocol
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from app.providers.base import ProviderToolCallContext
from app.protocol import ToolResultSubmission


class ToolResultConflictError(ValueError):
    pass


class ToolResultStore(Protocol):
    async def initialize(self) -> None: ...
    async def is_healthy(self) -> bool: ...
    async def record(
        self,
        conversation_id: UUID,
        result: ToolResultSubmission,
    ) -> bool: ...
    async def save_tool_call_context(
        self,
        conversation_id: UUID,
        context: ProviderToolCallContext,
    ) -> None: ...
    async def get_tool_call_context(
        self,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> ProviderToolCallContext | None: ...
    async def get_assistant_reply(
        self,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> str | None: ...
    async def save_assistant_reply(
        self,
        conversation_id: UUID,
        tool_call_id: str,
        reply: str,
    ) -> None: ...
    async def close(self) -> None: ...


class PostgreSQLToolResultStore:
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
                CREATE TABLE IF NOT EXISTS tool_results (
                    conversation_id UUID NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    task_id UUID NOT NULL,
                    capability TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    received_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (conversation_id, tool_call_id)
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_call_contexts (
                    conversation_id UUID NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    assistant_reply TEXT,
                    created_at TIMESTAMPTZ NOT NULL,
                    completed_at TIMESTAMPTZ,
                    PRIMARY KEY (conversation_id, tool_call_id)
                )
                """
            )

    async def is_healthy(self) -> bool:
        try:
            async with self._pool.connection(timeout=2) as connection:
                cursor = await connection.execute("SELECT 1")
                return await cursor.fetchone() == (1,)
        except Exception:
            return False

    async def save_tool_call_context(
        self,
        conversation_id: UUID,
        context: ProviderToolCallContext,
    ) -> None:
        context_json = _canonical_json(context.state)
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO tool_call_contexts (
                    conversation_id,
                    tool_call_id,
                    capability,
                    provider,
                    context_json,
                    created_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (conversation_id, tool_call_id) DO NOTHING
                RETURNING tool_call_id
                """,
                (
                    conversation_id,
                    context.tool_call_id,
                    context.capability,
                    context.provider,
                    context_json,
                    datetime.now(timezone.utc),
                ),
            )
            if await cursor.fetchone() is not None:
                return

            existing_cursor = await connection.execute(
                """
                SELECT capability, provider, context_json
                FROM tool_call_contexts
                WHERE conversation_id = %s AND tool_call_id = %s
                """,
                (conversation_id, context.tool_call_id),
            )
            existing = await existing_cursor.fetchone()
            expected = (context.capability, context.provider, context_json)
            if existing is None or existing != expected:
                raise ToolResultConflictError(
                    "A different context already exists for this Tool call"
                )

    async def get_tool_call_context(
        self,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> ProviderToolCallContext | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT capability, provider, context_json
                FROM tool_call_contexts
                WHERE conversation_id = %s AND tool_call_id = %s
                """,
                (conversation_id, tool_call_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return ProviderToolCallContext(
            tool_call_id=tool_call_id,
            capability=row[0],
            provider=row[1],
            state=json.loads(row[2]),
        )

    async def get_assistant_reply(
        self,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> str | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT assistant_reply
                FROM tool_call_contexts
                WHERE conversation_id = %s AND tool_call_id = %s
                """,
                (conversation_id, tool_call_id),
            )
            row = await cursor.fetchone()
        return None if row is None else row[0]

    async def save_assistant_reply(
        self,
        conversation_id: UUID,
        tool_call_id: str,
        reply: str,
    ) -> None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE tool_call_contexts
                SET assistant_reply = %s, completed_at = %s
                WHERE conversation_id = %s
                  AND tool_call_id = %s
                  AND assistant_reply IS NULL
                RETURNING tool_call_id
                """,
                (
                    reply,
                    datetime.now(timezone.utc),
                    conversation_id,
                    tool_call_id,
                ),
            )
            if await cursor.fetchone() is not None:
                return
            existing_cursor = await connection.execute(
                """
                SELECT assistant_reply
                FROM tool_call_contexts
                WHERE conversation_id = %s AND tool_call_id = %s
                """,
                (conversation_id, tool_call_id),
            )
            existing = await existing_cursor.fetchone()
            if existing is None or existing[0] != reply:
                raise ToolResultConflictError(
                    "A different assistant reply already exists for this Tool call"
                )

    async def record(
        self,
        conversation_id: UUID,
        result: ToolResultSubmission,
    ) -> bool:
        payload_json = _canonical_payload(result)
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO tool_results (
                    conversation_id,
                    tool_call_id,
                    task_id,
                    capability,
                    status,
                    payload_json,
                    received_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (conversation_id, tool_call_id) DO NOTHING
                RETURNING tool_call_id
                """,
                (
                    conversation_id,
                    result.tool_call_id,
                    result.task_id,
                    result.capability,
                    result.status,
                    payload_json,
                    datetime.now(timezone.utc),
                ),
            )
            if await cursor.fetchone() is not None:
                return True

            existing_cursor = await connection.execute(
                """
                SELECT payload_json
                FROM tool_results
                WHERE conversation_id = %s AND tool_call_id = %s
                """,
                (conversation_id, result.tool_call_id),
            )
            existing = await existing_cursor.fetchone()
            if existing is None or existing[0] != payload_json:
                raise ToolResultConflictError(
                    "A different result already exists for this Tool call"
                )
            return False

    async def close(self) -> None:
        await self._pool.close()


class InMemoryToolResultStore:
    def __init__(self) -> None:
        self._results: dict[tuple[UUID, str], str] = {}
        self._contexts: dict[tuple[UUID, str], ProviderToolCallContext] = {}
        self._replies: dict[tuple[UUID, str], str] = {}

    async def initialize(self) -> None:
        pass

    async def is_healthy(self) -> bool:
        return True

    async def record(
        self,
        conversation_id: UUID,
        result: ToolResultSubmission,
    ) -> bool:
        key = (conversation_id, result.tool_call_id)
        payload_json = _canonical_payload(result)
        existing = self._results.get(key)
        if existing is None:
            self._results[key] = payload_json
            return True
        if existing != payload_json:
            raise ToolResultConflictError(
                "A different result already exists for this Tool call"
            )
        return False

    async def save_tool_call_context(
        self,
        conversation_id: UUID,
        context: ProviderToolCallContext,
    ) -> None:
        key = (conversation_id, context.tool_call_id)
        existing = self._contexts.get(key)
        if existing is None:
            self._contexts[key] = context
            return
        if existing != context:
            raise ToolResultConflictError(
                "A different context already exists for this Tool call"
            )

    async def get_tool_call_context(
        self,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> ProviderToolCallContext | None:
        return self._contexts.get((conversation_id, tool_call_id))

    async def get_assistant_reply(
        self,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> str | None:
        return self._replies.get((conversation_id, tool_call_id))

    async def save_assistant_reply(
        self,
        conversation_id: UUID,
        tool_call_id: str,
        reply: str,
    ) -> None:
        key = (conversation_id, tool_call_id)
        if key not in self._contexts:
            raise ToolResultConflictError("Tool call context does not exist")
        existing = self._replies.get(key)
        if existing is None:
            self._replies[key] = reply
            return
        if existing != reply:
            raise ToolResultConflictError(
                "A different assistant reply already exists for this Tool call"
            )

    async def close(self) -> None:
        pass


def _canonical_payload(result: ToolResultSubmission) -> str:
    return _canonical_json(result.semantic_payload())


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
