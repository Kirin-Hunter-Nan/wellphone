"""PostgreSQL-backed durable server-task store."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.tasks.models import (
    DeviceToolCall,
    DeviceToolConflictError,
    ServerTask,
    ServerTaskArtifact,
    ServerTaskCreate,
    ServerTaskStep,
    TaskOutcome,
)
from app.tasks.steps import _steps_for

class PostgreSQLServerTaskStore:
    def __init__(self, database_url: str) -> None:
        self._pool = AsyncConnectionPool(
            conninfo=database_url, min_size=1, max_size=5, open=False,
            check=AsyncConnectionPool.check_connection,
        )

    async def initialize(self) -> None:
        await self._pool.open(wait=True, timeout=30)
        async with self._pool.connection() as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS server_tasks (
                    id UUID PRIMARY KEY, conversation_id UUID NOT NULL, capability TEXT NOT NULL,
                    tool_call_id TEXT,
                    title TEXT NOT NULL, input_json JSONB NOT NULL, status TEXT NOT NULL,
                    phase TEXT NOT NULL, progress DOUBLE PRECISION NOT NULL DEFAULT 0,
                    detail TEXT, result_summary TEXT, error_message TEXT,
                    requires_confirmation BOOLEAN NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
                    available_at TIMESTAMPTZ NOT NULL, lease_owner TEXT, lease_until TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
                )
            """)
            await connection.execute(
                "ALTER TABLE server_tasks ADD COLUMN IF NOT EXISTS tool_call_id TEXT"
            )
            await connection.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS server_tasks_tool_call_idx
                ON server_tasks (conversation_id, tool_call_id) WHERE tool_call_id IS NOT NULL
            """)
            await connection.execute("""
                CREATE INDEX IF NOT EXISTS server_tasks_claim_idx
                ON server_tasks (status, available_at, created_at)
            """)
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS server_task_steps (
                    id UUID PRIMARY KEY, task_id UUID NOT NULL REFERENCES server_tasks(id) ON DELETE CASCADE,
                    step_order INTEGER NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
                    detail TEXT, updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (task_id, step_order)
                )
            """)
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS task_artifacts (
                    id UUID PRIMARY KEY, task_id UUID NOT NULL REFERENCES server_tasks(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL, title TEXT NOT NULL, content_type TEXT NOT NULL,
                    payload_json JSONB, storage_reference TEXT, created_at TIMESTAMPTZ NOT NULL
                )
            """)
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS device_tool_calls (
                    task_id UUID NOT NULL REFERENCES server_tasks(id) ON DELETE CASCADE,
                    tool_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json JSONB NOT NULL,
                    status TEXT NOT NULL,
                    result_json JSONB,
                    error_code TEXT,
                    error_message TEXT,
                    requested_at TIMESTAMPTZ NOT NULL,
                    completed_at TIMESTAMPTZ,
                    PRIMARY KEY (task_id, tool_call_id)
                )
            """)
            await connection.execute("""
                CREATE INDEX IF NOT EXISTS device_tool_calls_pending_idx
                ON device_tool_calls (task_id, status, requested_at)
            """)

    async def is_healthy(self) -> bool:
        try:
            async with self._pool.connection(timeout=2) as connection:
                return await (await connection.execute("SELECT 1")).fetchone() == (1,)
        except Exception:
            return False

    async def create(self, conversation_id: UUID, request: ServerTaskCreate) -> ServerTask:
        task_id, now = uuid4(), datetime.now(timezone.utc)
        status = "waitingForConfirmation" if request.requires_confirmation else "queued"
        phase = "waitingForConfirmation" if request.requires_confirmation else "queued"
        detail = "等待用户确认" if request.requires_confirmation else "等待执行"
        async with self._pool.connection() as connection:
            await connection.execute("""
                INSERT INTO server_tasks (
                    id, conversation_id, capability, tool_call_id, title, input_json, status, phase,
                    progress, detail, requires_confirmation, available_at, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, 0, %s, %s, %s, %s, %s)
                ON CONFLICT (conversation_id, tool_call_id) WHERE tool_call_id IS NOT NULL DO NOTHING
            """, (task_id, conversation_id, request.capability, request.tool_call_id, request.title,
                  json.dumps(request.input, ensure_ascii=False), status, phase, detail,
                  request.requires_confirmation, now, now, now))
            if request.tool_call_id:
                cursor = await connection.execute(
                    "SELECT id FROM server_tasks WHERE conversation_id=%s AND tool_call_id=%s",
                    (conversation_id, request.tool_call_id),
                )
                task_id = (await cursor.fetchone())[0]
        task = await self.get(task_id)
        assert task is not None
        return task

    async def get(self, task_id: UUID) -> ServerTask | None:
        async with self._pool.connection() as connection:
            return await self._read_task(connection, task_id)

    async def list(self, conversation_id: UUID) -> list[ServerTask]:
        async with self._dict_connection() as connection:
            rows = await (await connection.execute(
                "SELECT * FROM server_tasks WHERE conversation_id = %s ORDER BY created_at DESC",
                (conversation_id,),
            )).fetchall()
            return [await self._hydrate(connection, row) for row in rows]

    async def confirm(self, task_id: UUID) -> ServerTask | None:
        async with self._pool.connection() as connection:
            await connection.execute("""
                UPDATE server_tasks SET status='queued', phase='queued', detail='等待执行',
                    available_at=NOW(), updated_at=NOW()
                WHERE id=%s AND status='waitingForConfirmation'
            """, (task_id,))
        return await self.get(task_id)

    async def cancel(self, task_id: UUID) -> ServerTask | None:
        async with self._pool.connection() as connection:
            await connection.execute("""
                UPDATE server_tasks SET status='cancelled', phase='cancelled', detail='任务已取消',
                    lease_owner=NULL, lease_until=NULL, updated_at=NOW()
                WHERE id=%s AND status NOT IN ('completed','failed','cancelled')
            """, (task_id,))
        return await self.get(task_id)

    async def claim(self, worker_id: str, lease_seconds: int) -> ServerTask | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute("""
                WITH candidate AS (
                    SELECT id FROM server_tasks
                    WHERE status IN ('queued','running') AND available_at <= NOW()
                      AND (lease_until IS NULL OR lease_until <= NOW())
                    ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE server_tasks AS task
                SET status='running', phase='starting', detail='正在启动',
                    attempt_count=attempt_count+1, lease_owner=%s,
                    lease_until=NOW() + (%s * INTERVAL '1 second'), updated_at=NOW()
                FROM candidate WHERE task.id=candidate.id RETURNING task.id
            """, (worker_id, lease_seconds))
            row = await cursor.fetchone()
            if row is None:
                return None
            return await self._read_task(connection, row[0])

    async def update_progress(
        self, task_id: UUID, worker_id: str, *, phase: str, progress: float,
        detail: str, steps: Sequence[str], current_step: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._pool.connection() as connection:
            cursor = await connection.execute("""
                UPDATE server_tasks SET phase=%s, progress=%s, detail=%s, updated_at=%s
                WHERE id=%s AND status='running' AND lease_owner=%s RETURNING id
            """, (phase, min(max(progress, 0), 1), detail, now, task_id, worker_id))
            if await cursor.fetchone() is None:
                return
            for step in _steps_for(task_id, steps, current_step, detail):
                await connection.execute("""
                    INSERT INTO server_task_steps (id, task_id, step_order, title, status, detail, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (task_id, step_order) DO UPDATE
                    SET title=EXCLUDED.title,status=EXCLUDED.status,detail=EXCLUDED.detail,updated_at=EXCLUDED.updated_at
                """, (step.id, task_id, step.order, step.title, step.status, step.detail, now))

    async def renew(self, task_id: UUID, worker_id: str, lease_seconds: int) -> bool:
        async with self._pool.connection() as connection:
            cursor = await connection.execute("""
                UPDATE server_tasks
                SET lease_until=NOW() + (%s * INTERVAL '1 second'),updated_at=NOW()
                WHERE id=%s AND status='running' AND lease_owner=%s RETURNING id
            """, (lease_seconds, task_id, worker_id))
            return await cursor.fetchone() is not None

    async def complete(self, task_id: UUID, worker_id: str, outcome: TaskOutcome) -> None:
        now = datetime.now(timezone.utc)
        async with self._pool.connection() as connection:
            cursor = await connection.execute("""
                UPDATE server_tasks SET status='completed',phase='completed',progress=1,
                    detail=%s,result_summary=%s,error_message=NULL,lease_owner=NULL,lease_until=NULL,updated_at=%s
                WHERE id=%s AND lease_owner=%s RETURNING id
            """, (outcome.summary, outcome.summary, now, task_id, worker_id))
            if await cursor.fetchone() is None:
                return
            await connection.execute(
                "UPDATE server_task_steps SET status='completed',updated_at=%s WHERE task_id=%s",
                (now, task_id),
            )
            for item in outcome.artifacts:
                await connection.execute("""
                    INSERT INTO task_artifacts
                    (id,task_id,kind,title,content_type,payload_json,storage_reference,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                """, (uuid4(), task_id, item.kind, item.title, item.content_type,
                      json.dumps(item.payload, ensure_ascii=False) if item.payload is not None else None,
                      item.storage_reference, now))

    async def fail(
        self, task_id: UUID, worker_id: str, message: str, *, retry: bool,
        retry_delay_seconds: int,
    ) -> None:
        status, phase, detail = ("queued", "retrying", "等待重试") if retry else ("failed", "failed", message)
        async with self._pool.connection() as connection:
            await connection.execute("""
                UPDATE server_tasks SET status=%s,phase=%s,detail=%s,error_message=%s,
                    available_at=NOW() + (%s * INTERVAL '1 second'),lease_owner=NULL,lease_until=NULL,updated_at=NOW()
                WHERE id=%s AND lease_owner=%s
            """, (status, phase, detail, message, retry_delay_seconds, task_id, worker_id))

    async def enqueue_device_tool(
        self, task_id: UUID, tool_call_id: str, tool_name: str,
        arguments: dict[str, object],
    ) -> DeviceToolCall:
        now = datetime.now(timezone.utc)
        encoded_arguments = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
        async with self._dict_connection() as connection:
            await connection.execute("""
                INSERT INTO device_tool_calls (
                    task_id, tool_call_id, tool_name, arguments_json,
                    status, requested_at
                ) VALUES (%s, %s, %s, %s::jsonb, 'pending', %s)
                ON CONFLICT (task_id, tool_call_id) DO NOTHING
            """, (task_id, tool_call_id, tool_name, encoded_arguments, now))
            row = await (await connection.execute("""
                SELECT * FROM device_tool_calls
                WHERE task_id=%s AND tool_call_id=%s
            """, (task_id, tool_call_id))).fetchone()
            if row is None:
                raise KeyError(f"Task {task_id} does not exist")
            call = self._device_tool_from_row(row)
            if call.tool_name != tool_name or call.arguments != arguments:
                raise DeviceToolConflictError(
                    "Device Tool call id identifies different arguments"
                )
            return call

    async def get_pending_device_tool(self, task_id: UUID) -> DeviceToolCall | None:
        async with self._dict_connection() as connection:
            row = await (await connection.execute("""
                SELECT * FROM device_tool_calls
                WHERE task_id=%s AND status='pending'
                ORDER BY requested_at LIMIT 1
            """, (task_id,))).fetchone()
            return self._device_tool_from_row(row) if row else None

    async def get_device_tool(
        self, task_id: UUID, tool_call_id: str,
    ) -> DeviceToolCall | None:
        async with self._dict_connection() as connection:
            row = await (await connection.execute("""
                SELECT * FROM device_tool_calls
                WHERE task_id=%s AND tool_call_id=%s
            """, (task_id, tool_call_id))).fetchone()
            return self._device_tool_from_row(row) if row else None

    async def submit_device_tool_result(
        self, task_id: UUID, tool_call_id: str, *,
        result: dict[str, object] | None,
        error_code: str | None,
        error_message: str | None,
    ) -> DeviceToolCall | None:
        desired_status = "completed" if result is not None else "failed"
        encoded_result = (
            json.dumps(result, ensure_ascii=False, sort_keys=True)
            if result is not None else None
        )
        async with self._dict_connection() as connection:
            row = await (await connection.execute("""
                SELECT * FROM device_tool_calls
                WHERE task_id=%s AND tool_call_id=%s FOR UPDATE
            """, (task_id, tool_call_id))).fetchone()
            if row is None:
                return None
            call = self._device_tool_from_row(row)
            if call.status != "pending":
                if (
                    call.status != desired_status
                    or call.result != result
                    or call.error_code != error_code
                    or call.error_message != error_message
                ):
                    raise DeviceToolConflictError(
                        "Device Tool result differs from the persisted result"
                    )
                return call
            row = await (await connection.execute("""
                UPDATE device_tool_calls
                SET status=%s, result_json=%s::jsonb, error_code=%s,
                    error_message=%s, completed_at=NOW()
                WHERE task_id=%s AND tool_call_id=%s
                RETURNING *
            """, (
                desired_status, encoded_result, error_code, error_message,
                task_id, tool_call_id,
            ))).fetchone()
            assert row is not None
            return self._device_tool_from_row(row)

    @asynccontextmanager
    async def _dict_connection(self):
        """Borrow a pooled connection without leaking its row factory."""
        async with self._pool.connection() as connection:
            previous_factory = connection.row_factory
            connection.row_factory = dict_row
            try:
                yield connection
            finally:
                connection.row_factory = previous_factory

    async def _read_task(self, connection, task_id: UUID) -> ServerTask | None:
        previous_factory = connection.row_factory
        connection.row_factory = dict_row
        try:
            row = await (await connection.execute("SELECT * FROM server_tasks WHERE id=%s", (task_id,))).fetchone()
            return await self._hydrate(connection, row) if row else None
        finally:
            connection.row_factory = previous_factory

    async def _hydrate(self, connection, row: dict) -> ServerTask:
        steps = await (await connection.execute(
            "SELECT * FROM server_task_steps WHERE task_id=%s ORDER BY step_order", (row["id"],)
        )).fetchall()
        artifacts = await (await connection.execute(
            "SELECT * FROM task_artifacts WHERE task_id=%s ORDER BY created_at", (row["id"],)
        )).fetchall()
        return ServerTask(
            id=row["id"], conversationId=row["conversation_id"], capability=row["capability"],
            toolCallId=row.get("tool_call_id"),
            title=row["title"], input=row["input_json"], status=row["status"], phase=row["phase"],
            progress=row["progress"], detail=row["detail"], resultSummary=row["result_summary"],
            errorMessage=row["error_message"], requiresConfirmation=row["requires_confirmation"],
            attemptCount=row["attempt_count"], createdAt=row["created_at"], updatedAt=row["updated_at"],
            steps=[ServerTaskStep(id=item["id"], order=item["step_order"], title=item["title"],
                                  status=item["status"], detail=item["detail"]) for item in steps],
            artifacts=[ServerTaskArtifact(
                id=item["id"], taskId=item["task_id"], kind=item["kind"], title=item["title"],
                contentType=item["content_type"], payload=item["payload_json"],
                storageReference=item["storage_reference"], createdAt=item["created_at"],
            ) for item in artifacts],
        )

    @staticmethod
    def _device_tool_from_row(row: dict) -> DeviceToolCall:
        return DeviceToolCall(
            taskId=row["task_id"],
            toolCallId=row["tool_call_id"],
            toolName=row["tool_name"],
            arguments=row["arguments_json"],
            status=row["status"],
            result=row["result_json"],
            errorCode=row["error_code"],
            errorMessage=row["error_message"],
            requestedAt=row["requested_at"],
            completedAt=row["completed_at"],
        )

    async def close(self) -> None:
        await self._pool.close()
