from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Protocol
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from psycopg.errors import UniqueViolation

from app.protocol import TaskCheckpointSubmission


class TaskCheckpointConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TaskCheckpointRecord:
    duplicate: bool
    applied: bool
    current_revision: int
    gap: bool


class TaskCheckpointStore(Protocol):
    async def initialize(self) -> None: ...
    async def is_healthy(self) -> bool: ...
    async def record(
        self,
        conversation_id: UUID,
        checkpoint: TaskCheckpointSubmission,
    ) -> TaskCheckpointRecord: ...
    async def close(self) -> None: ...


class PostgreSQLTaskCheckpointStore:
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
                CREATE TABLE IF NOT EXISTS task_checkpoints (
                    conversation_id UUID NOT NULL,
                    task_id UUID NOT NULL,
                    revision INTEGER NOT NULL,
                    request_id TEXT NOT NULL,
                    payload_fingerprint TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at TIMESTAMPTZ NOT NULL,
                    received_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (conversation_id, task_id, revision),
                    UNIQUE (conversation_id, request_id)
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_task_snapshots (
                    conversation_id UUID NOT NULL,
                    task_id UUID NOT NULL,
                    revision INTEGER NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    progress DOUBLE PRECISION,
                    detail TEXT,
                    result_summary TEXT,
                    error_message TEXT,
                    execution_attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_execution_retry_at TIMESTAMPTZ,
                    last_execution_error_message TEXT,
                    cancellation_requested_at TIMESTAMPTZ,
                    execution_deadline_at TIMESTAMPTZ,
                    occurred_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (conversation_id, task_id),
                    UNIQUE (conversation_id, tool_call_id)
                )
                """
            )
            await connection.execute(
                """
                ALTER TABLE agent_task_snapshots
                    ADD COLUMN IF NOT EXISTS execution_attempt_count
                    INTEGER NOT NULL DEFAULT 0
                """
            )
            await connection.execute(
                """
                ALTER TABLE agent_task_snapshots
                    ADD COLUMN IF NOT EXISTS next_execution_retry_at TIMESTAMPTZ
                """
            )
            await connection.execute(
                """
                ALTER TABLE agent_task_snapshots
                    ADD COLUMN IF NOT EXISTS last_execution_error_message TEXT
                """
            )
            await connection.execute(
                """
                ALTER TABLE agent_task_snapshots
                    ADD COLUMN IF NOT EXISTS cancellation_requested_at TIMESTAMPTZ
                """
            )
            await connection.execute(
                """
                ALTER TABLE agent_task_snapshots
                    ADD COLUMN IF NOT EXISTS execution_deadline_at TIMESTAMPTZ
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
        checkpoint: TaskCheckpointSubmission,
    ) -> TaskCheckpointRecord:
        fingerprint = _fingerprint(checkpoint)
        payload_json = json.dumps(
            checkpoint.semantic_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        now = datetime.now(timezone.utc)
        async with self._pool.connection() as connection:
            identity_cursor = await connection.execute(
                """
                SELECT task_id, capability
                FROM agent_task_snapshots
                WHERE conversation_id = %s AND tool_call_id = %s
                """,
                (conversation_id, checkpoint.tool_call_id),
            )
            existing_identity = await identity_cursor.fetchone()
            if existing_identity is not None and (
                existing_identity[0] != checkpoint.task_id
                or existing_identity[1] != checkpoint.capability
            ):
                raise TaskCheckpointConflictError(
                    "toolCallId is already associated with a different task"
                )

            cursor = await connection.execute(
                """
                INSERT INTO task_checkpoints (
                    conversation_id, task_id, revision, request_id,
                    payload_fingerprint, payload_json, occurred_at, received_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING revision
                """,
                (
                    conversation_id,
                    checkpoint.task_id,
                    checkpoint.revision,
                    checkpoint.request_id,
                    fingerprint,
                    payload_json,
                    checkpoint.occurred_at,
                    now,
                ),
            )
            inserted = await cursor.fetchone() is not None
            if not inserted:
                existing_cursor = await connection.execute(
                    """
                    SELECT payload_fingerprint
                    FROM task_checkpoints
                    WHERE conversation_id = %s
                      AND (request_id = %s OR (task_id = %s AND revision = %s))
                    LIMIT 1
                    """,
                    (
                        conversation_id,
                        checkpoint.request_id,
                        checkpoint.task_id,
                        checkpoint.revision,
                    ),
                )
                existing = await existing_cursor.fetchone()
                if existing is None or existing[0] != fingerprint:
                    raise TaskCheckpointConflictError(
                        "Checkpoint requestId or revision has different content"
                    )

            snapshot_cursor = await connection.execute(
                """
                SELECT revision FROM agent_task_snapshots
                WHERE conversation_id = %s AND task_id = %s
                """,
                (conversation_id, checkpoint.task_id),
            )
            previous = await snapshot_cursor.fetchone()
            previous_revision = previous[0] if previous is not None else 0
            applied = checkpoint.revision > previous_revision
            gap = checkpoint.revision > previous_revision + 1
            if applied:
                try:
                    applied_cursor = await connection.execute(
                        """
                        INSERT INTO agent_task_snapshots (
                            conversation_id, task_id, revision, tool_call_id, capability,
                            status, phase, progress, detail, result_summary, error_message,
                            execution_attempt_count, next_execution_retry_at,
                            last_execution_error_message, cancellation_requested_at,
                            execution_deadline_at, occurred_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (conversation_id, task_id) DO UPDATE
                        SET revision = EXCLUDED.revision,
                            tool_call_id = EXCLUDED.tool_call_id,
                            capability = EXCLUDED.capability,
                            status = EXCLUDED.status,
                            phase = EXCLUDED.phase,
                            progress = EXCLUDED.progress,
                            detail = EXCLUDED.detail,
                            result_summary = EXCLUDED.result_summary,
                            error_message = EXCLUDED.error_message,
                            execution_attempt_count = EXCLUDED.execution_attempt_count,
                            next_execution_retry_at = EXCLUDED.next_execution_retry_at,
                            last_execution_error_message = EXCLUDED.last_execution_error_message,
                            cancellation_requested_at = EXCLUDED.cancellation_requested_at,
                            execution_deadline_at = EXCLUDED.execution_deadline_at,
                            occurred_at = EXCLUDED.occurred_at,
                            updated_at = EXCLUDED.updated_at
                        WHERE agent_task_snapshots.revision < EXCLUDED.revision
                        RETURNING revision
                        """,
                        (
                            conversation_id,
                            checkpoint.task_id,
                            checkpoint.revision,
                            checkpoint.tool_call_id,
                            checkpoint.capability,
                            checkpoint.status,
                            checkpoint.phase,
                            checkpoint.progress,
                            checkpoint.detail,
                            checkpoint.result_summary,
                            checkpoint.error_message,
                            checkpoint.execution_attempt_count,
                            checkpoint.next_execution_retry_at,
                            checkpoint.last_execution_error_message,
                            checkpoint.cancellation_requested_at,
                            checkpoint.execution_deadline_at,
                            checkpoint.occurred_at,
                            now,
                        ),
                    )
                    applied = await applied_cursor.fetchone() is not None
                except UniqueViolation as error:
                    raise TaskCheckpointConflictError(
                        "toolCallId is already associated with a different task"
                    ) from error
            current_cursor = await connection.execute(
                """
                SELECT revision FROM agent_task_snapshots
                WHERE conversation_id = %s AND task_id = %s
                """,
                (conversation_id, checkpoint.task_id),
            )
            current = await current_cursor.fetchone()
            current_revision = current[0] if current is not None else previous_revision
            gap = applied and gap
        return TaskCheckpointRecord(
            duplicate=not inserted,
            applied=applied,
            current_revision=current_revision,
            gap=gap,
        )

    async def close(self) -> None:
        await self._pool.close()


class InMemoryTaskCheckpointStore:
    def __init__(self) -> None:
        self._events: dict[tuple[UUID, UUID, int], tuple[str, str]] = {}
        self._request_ids: dict[tuple[UUID, str], tuple[str, int]] = {}
        self._revisions: dict[tuple[UUID, UUID], int] = {}
        self._tool_calls: dict[tuple[UUID, str], tuple[UUID, str]] = {}

    async def initialize(self) -> None:
        pass

    async def is_healthy(self) -> bool:
        return True

    async def record(
        self,
        conversation_id: UUID,
        checkpoint: TaskCheckpointSubmission,
    ) -> TaskCheckpointRecord:
        fingerprint = _fingerprint(checkpoint)
        tool_call_key = (conversation_id, checkpoint.tool_call_id)
        existing_identity = self._tool_calls.get(tool_call_key)
        identity = (checkpoint.task_id, checkpoint.capability)
        if existing_identity is not None and existing_identity != identity:
            raise TaskCheckpointConflictError(
                "toolCallId is already associated with a different task"
            )
        event_key = (conversation_id, checkpoint.task_id, checkpoint.revision)
        request_key = (conversation_id, checkpoint.request_id)
        existing_event = self._events.get(event_key)
        existing_request = self._request_ids.get(request_key)
        if existing_event is not None or existing_request is not None:
            existing_fingerprint = (
                existing_event[0] if existing_event is not None else existing_request[0]
            )
            if existing_fingerprint != fingerprint:
                raise TaskCheckpointConflictError(
                    "Checkpoint requestId or revision has different content"
                )
            current = self._revisions.get((conversation_id, checkpoint.task_id), 0)
            return TaskCheckpointRecord(True, False, current, False)

        self._events[event_key] = (fingerprint, checkpoint.request_id)
        self._request_ids[request_key] = (fingerprint, checkpoint.revision)
        snapshot_key = (conversation_id, checkpoint.task_id)
        previous = self._revisions.get(snapshot_key, 0)
        applied = checkpoint.revision > previous
        gap = checkpoint.revision > previous + 1
        if applied:
            self._revisions[snapshot_key] = checkpoint.revision
            self._tool_calls[tool_call_key] = identity
        return TaskCheckpointRecord(
            duplicate=False,
            applied=applied,
            current_revision=max(previous, checkpoint.revision),
            gap=gap,
        )

    async def close(self) -> None:
        pass


def _fingerprint(checkpoint: TaskCheckpointSubmission) -> str:
    canonical = json.dumps(
        checkpoint.semantic_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
