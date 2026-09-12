"""PostgreSQL-backed task checkpoint persistence."""

from datetime import datetime, timezone
import json
from uuid import UUID

from psycopg.errors import UniqueViolation
from psycopg_pool import AsyncConnectionPool

from app.api.protocol import TaskCheckpointSubmission
from app.tasks.checkpoints.canonical import checkpoint_fingerprint
from app.tasks.checkpoints.models import TaskCheckpointConflictError, TaskCheckpointRecord


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
            for statement in (
                """ALTER TABLE agent_task_snapshots
                    ADD COLUMN IF NOT EXISTS execution_attempt_count
                    INTEGER NOT NULL DEFAULT 0""",
                """ALTER TABLE agent_task_snapshots
                    ADD COLUMN IF NOT EXISTS next_execution_retry_at TIMESTAMPTZ""",
                """ALTER TABLE agent_task_snapshots
                    ADD COLUMN IF NOT EXISTS last_execution_error_message TEXT""",
                """ALTER TABLE agent_task_snapshots
                    ADD COLUMN IF NOT EXISTS cancellation_requested_at TIMESTAMPTZ""",
                """ALTER TABLE agent_task_snapshots
                    ADD COLUMN IF NOT EXISTS execution_deadline_at TIMESTAMPTZ""",
            ):
                await connection.execute(statement)

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
        fingerprint = checkpoint_fingerprint(checkpoint)
        payload_json = json.dumps(
            checkpoint.semantic_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        now = datetime.now(timezone.utc)
        async with self._pool.connection() as connection:
            await self._validate_tool_call_identity(connection, conversation_id, checkpoint)
            inserted = await self._insert_event(
                connection, conversation_id, checkpoint, fingerprint, payload_json, now
            )
            if not inserted:
                await self._validate_duplicate(
                    connection, conversation_id, checkpoint, fingerprint
                )

            previous_revision = await self._current_revision(
                connection, conversation_id, checkpoint.task_id
            )
            applied = checkpoint.revision > previous_revision
            gap = checkpoint.revision > previous_revision + 1
            if applied:
                applied = await self._apply_snapshot(
                    connection, conversation_id, checkpoint, now
                )
            current_revision = await self._current_revision(
                connection, conversation_id, checkpoint.task_id
            )

        return TaskCheckpointRecord(
            duplicate=not inserted,
            applied=applied,
            current_revision=current_revision,
            gap=applied and gap,
        )

    async def _validate_tool_call_identity(
        self, connection, conversation_id: UUID, checkpoint: TaskCheckpointSubmission
    ) -> None:
        cursor = await connection.execute(
            """SELECT task_id, capability FROM agent_task_snapshots
            WHERE conversation_id = %s AND tool_call_id = %s""",
            (conversation_id, checkpoint.tool_call_id),
        )
        existing = await cursor.fetchone()
        if existing is not None and (
            existing[0] != checkpoint.task_id or existing[1] != checkpoint.capability
        ):
            raise TaskCheckpointConflictError(
                "toolCallId is already associated with a different task"
            )

    async def _insert_event(
        self, connection, conversation_id: UUID, checkpoint: TaskCheckpointSubmission,
        fingerprint: str, payload_json: str, now: datetime,
    ) -> bool:
        cursor = await connection.execute(
            """
            INSERT INTO task_checkpoints (
                conversation_id, task_id, revision, request_id,
                payload_fingerprint, payload_json, occurred_at, received_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING RETURNING revision
            """,
            (conversation_id, checkpoint.task_id, checkpoint.revision,
             checkpoint.request_id, fingerprint, payload_json, checkpoint.occurred_at, now),
        )
        return await cursor.fetchone() is not None

    async def _validate_duplicate(
        self, connection, conversation_id: UUID, checkpoint: TaskCheckpointSubmission,
        fingerprint: str,
    ) -> None:
        cursor = await connection.execute(
            """SELECT payload_fingerprint FROM task_checkpoints
            WHERE conversation_id = %s
              AND (request_id = %s OR (task_id = %s AND revision = %s))
            LIMIT 1""",
            (conversation_id, checkpoint.request_id, checkpoint.task_id, checkpoint.revision),
        )
        existing = await cursor.fetchone()
        if existing is None or existing[0] != fingerprint:
            raise TaskCheckpointConflictError(
                "Checkpoint requestId or revision has different content"
            )

    async def _current_revision(
        self, connection, conversation_id: UUID, task_id: UUID
    ) -> int:
        cursor = await connection.execute(
            """SELECT revision FROM agent_task_snapshots
            WHERE conversation_id = %s AND task_id = %s""",
            (conversation_id, task_id),
        )
        current = await cursor.fetchone()
        return current[0] if current is not None else 0

    async def _apply_snapshot(
        self, connection, conversation_id: UUID, checkpoint: TaskCheckpointSubmission,
        now: datetime,
    ) -> bool:
        try:
            cursor = await connection.execute(
                """
                INSERT INTO agent_task_snapshots (
                    conversation_id, task_id, revision, tool_call_id, capability,
                    status, phase, progress, detail, result_summary, error_message,
                    execution_attempt_count, next_execution_retry_at,
                    last_execution_error_message, cancellation_requested_at,
                    execution_deadline_at, occurred_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
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
                (conversation_id, checkpoint.task_id, checkpoint.revision,
                 checkpoint.tool_call_id, checkpoint.capability, checkpoint.status,
                 checkpoint.phase, checkpoint.progress, checkpoint.detail,
                 checkpoint.result_summary, checkpoint.error_message,
                 checkpoint.execution_attempt_count, checkpoint.next_execution_retry_at,
                 checkpoint.last_execution_error_message,
                 checkpoint.cancellation_requested_at, checkpoint.execution_deadline_at,
                 checkpoint.occurred_at, now),
            )
            return await cursor.fetchone() is not None
        except UniqueViolation as error:
            raise TaskCheckpointConflictError(
                "toolCallId is already associated with a different task"
            ) from error

    async def close(self) -> None:
        await self._pool.close()
