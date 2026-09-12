"""Durable Agent Loop event journal implementations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Protocol
from uuid import UUID

from psycopg_pool import AsyncConnectionPool


@dataclass(frozen=True, slots=True)
class LoopJournalEvent:
    sequence: int
    event_type: str
    payload: dict[str, object]


class AgentLoopJournal(Protocol):
    async def initialize(self) -> None: ...
    async def load(self, task_id: UUID) -> list[LoopJournalEvent]: ...
    async def append(
        self, task_id: UUID, event_type: str, payload: dict[str, object]
    ) -> LoopJournalEvent: ...
    async def close(self) -> None: ...


class InMemoryAgentLoopJournal:
    def __init__(self) -> None:
        self._events: dict[UUID, list[LoopJournalEvent]] = {}

    async def initialize(self) -> None:
        pass

    async def load(self, task_id: UUID) -> list[LoopJournalEvent]:
        return list(self._events.get(task_id, []))

    async def append(
        self, task_id: UUID, event_type: str, payload: dict[str, object]
    ) -> LoopJournalEvent:
        events = self._events.setdefault(task_id, [])
        event = LoopJournalEvent(len(events) + 1, event_type, payload)
        events.append(event)
        return event

    async def close(self) -> None:
        pass


class PostgreSQLAgentLoopJournal:
    def __init__(self, database_url: str) -> None:
        self._pool = AsyncConnectionPool(
            conninfo=database_url, min_size=1, max_size=3, open=False,
            check=AsyncConnectionPool.check_connection,
        )

    async def initialize(self) -> None:
        await self._pool.open(wait=True, timeout=30)
        async with self._pool.connection() as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS agent_loop_events (
                    task_id UUID NOT NULL REFERENCES server_tasks(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (task_id, sequence)
                )
            """)

    async def load(self, task_id: UUID) -> list[LoopJournalEvent]:
        async with self._pool.connection() as connection:
            rows = await (await connection.execute(
                "SELECT sequence,event_type,payload_json FROM agent_loop_events "
                "WHERE task_id=%s ORDER BY sequence",
                (task_id,),
            )).fetchall()
        return [LoopJournalEvent(row[0], row[1], row[2]) for row in rows]

    async def append(
        self, task_id: UUID, event_type: str, payload: dict[str, object]
    ) -> LoopJournalEvent:
        now = datetime.now(timezone.utc)
        async with self._pool.connection() as connection:
            cursor = await connection.execute("""
                INSERT INTO agent_loop_events (task_id,sequence,event_type,payload_json,created_at)
                SELECT %s,COALESCE(MAX(sequence),0)+1,%s,%s::jsonb,%s
                FROM agent_loop_events WHERE task_id=%s
                RETURNING sequence
            """, (
                task_id, event_type,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now, task_id,
            ))
            sequence = (await cursor.fetchone())[0]
        return LoopJournalEvent(sequence, event_type, payload)

    async def close(self) -> None:
        await self._pool.close()
