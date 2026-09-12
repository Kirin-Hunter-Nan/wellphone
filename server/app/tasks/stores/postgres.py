"""PostgreSQL-backed durable server-task store."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.tasks.models import (
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
        async with self._pool.connection(row_factory=dict_row) as connection:
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

    async def close(self) -> None:
        await self._pool.close()
