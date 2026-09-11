from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Protocol
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

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

    async def is_healthy(self) -> bool:
        try:
            async with self._pool.connection(timeout=2) as connection:
                cursor = await connection.execute("SELECT 1")
                return await cursor.fetchone() == (1,)
        except Exception:
            return False

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

    async def close(self) -> None:
        pass


def _canonical_payload(result: ToolResultSubmission) -> str:
    return json.dumps(
        result.semantic_payload(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
