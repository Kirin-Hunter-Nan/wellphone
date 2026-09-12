from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Literal, Protocol
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field


TaskStatus = Literal[
    "waitingForConfirmation", "queued", "running", "completed", "failed", "cancelled"
]


class ServerTaskCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    capability: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    input: dict[str, object]
    requires_confirmation: bool = Field(default=True, alias="requiresConfirmation")
    tool_call_id: str | None = Field(default=None, alias="toolCallId", min_length=1)


class ServerTaskStep(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    order: int
    title: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    detail: str | None = None


class ServerTaskArtifact(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    task_id: UUID = Field(alias="taskId")
    kind: str
    title: str
    content_type: str = Field(alias="contentType")
    payload: object | None = None
    storage_reference: str | None = Field(default=None, alias="storageReference")
    created_at: datetime = Field(alias="createdAt")


class ServerTask(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    conversation_id: UUID = Field(alias="conversationId")
    capability: str
    tool_call_id: str | None = Field(default=None, alias="toolCallId")
    title: str
    input: dict[str, object]
    status: TaskStatus
    phase: str
    progress: float = Field(ge=0, le=1)
    detail: str | None = None
    result_summary: str | None = Field(default=None, alias="resultSummary")
    error_message: str | None = Field(default=None, alias="errorMessage")
    requires_confirmation: bool = Field(alias="requiresConfirmation")
    attempt_count: int = Field(alias="attemptCount")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    steps: list[ServerTaskStep] = []
    artifacts: list[ServerTaskArtifact] = []


@dataclass(frozen=True, slots=True)
class ArtifactDraft:
    kind: str
    title: str
    content_type: str
    payload: object | None = None
    storage_reference: str | None = None


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    summary: str
    artifacts: tuple[ArtifactDraft, ...] = ()


class ServerTaskStore(Protocol):
    async def initialize(self) -> None: ...
    async def is_healthy(self) -> bool: ...
    async def create(self, conversation_id: UUID, request: ServerTaskCreate) -> ServerTask: ...
    async def get(self, task_id: UUID) -> ServerTask | None: ...
    async def list(self, conversation_id: UUID) -> list[ServerTask]: ...
    async def confirm(self, task_id: UUID) -> ServerTask | None: ...
    async def cancel(self, task_id: UUID) -> ServerTask | None: ...
    async def claim(self, worker_id: str, lease_seconds: int) -> ServerTask | None: ...
    async def update_progress(
        self, task_id: UUID, worker_id: str, *, phase: str, progress: float,
        detail: str, steps: Sequence[str], current_step: int,
    ) -> None: ...
    async def renew(self, task_id: UUID, worker_id: str, lease_seconds: int) -> bool: ...
    async def complete(self, task_id: UUID, worker_id: str, outcome: TaskOutcome) -> None: ...
    async def fail(
        self, task_id: UUID, worker_id: str, message: str, *, retry: bool,
        retry_delay_seconds: int,
    ) -> None: ...
    async def close(self) -> None: ...


class InMemoryServerTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[UUID, ServerTask] = {}
        self._leases: dict[UUID, tuple[str, datetime]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        pass

    async def is_healthy(self) -> bool:
        return True

    async def create(self, conversation_id: UUID, request: ServerTaskCreate) -> ServerTask:
        now = datetime.now(timezone.utc)
        if request.tool_call_id:
            existing = next((
                task for task in self._tasks.values()
                if task.conversation_id == conversation_id
                and task.tool_call_id == request.tool_call_id
            ), None)
            if existing:
                return existing.model_copy(deep=True)
        status: TaskStatus = "waitingForConfirmation" if request.requires_confirmation else "queued"
        task = ServerTask(
            id=uuid4(), conversationId=conversation_id, capability=request.capability,
            toolCallId=request.tool_call_id,
            title=request.title, input=request.input, status=status,
            phase="waitingForConfirmation" if request.requires_confirmation else "queued",
            progress=0, detail="等待用户确认" if request.requires_confirmation else "等待执行",
            requiresConfirmation=request.requires_confirmation, attemptCount=0,
            createdAt=now, updatedAt=now,
        )
        async with self._lock:
            self._tasks[task.id] = task
        return task.model_copy(deep=True)

    async def get(self, task_id: UUID) -> ServerTask | None:
        task = self._tasks.get(task_id)
        return task.model_copy(deep=True) if task else None

    async def list(self, conversation_id: UUID) -> list[ServerTask]:
        return [
            task.model_copy(deep=True)
            for task in sorted(self._tasks.values(), key=lambda value: value.created_at, reverse=True)
            if task.conversation_id == conversation_id
        ]

    async def confirm(self, task_id: UUID) -> ServerTask | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == "waitingForConfirmation":
                task.status, task.phase, task.detail = "queued", "queued", "等待执行"
                task.updated_at = datetime.now(timezone.utc)
            return task.model_copy(deep=True) if task else None

    async def cancel(self, task_id: UUID) -> ServerTask | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status not in ("completed", "failed", "cancelled"):
                task.status, task.phase, task.detail = "cancelled", "cancelled", "任务已取消"
                task.updated_at = datetime.now(timezone.utc)
            return task.model_copy(deep=True) if task else None

    async def claim(self, worker_id: str, lease_seconds: int) -> ServerTask | None:
        now = datetime.now(timezone.utc)
        async with self._lock:
            candidates = [
                task for task in self._tasks.values()
                if task.status in ("queued", "running")
                and (task.id not in self._leases or self._leases[task.id][1] <= now)
            ]
            if not candidates:
                return None
            task = min(candidates, key=lambda value: value.created_at)
            task.status, task.phase, task.detail = "running", "starting", "正在启动"
            task.attempt_count += 1
            task.updated_at = now
            self._leases[task.id] = (worker_id, now + timedelta(seconds=lease_seconds))
            return task.model_copy(deep=True)

    def _owns(self, task_id: UUID, worker_id: str) -> bool:
        lease = self._leases.get(task_id)
        return lease is not None and lease[0] == worker_id

    async def update_progress(
        self, task_id: UUID, worker_id: str, *, phase: str, progress: float,
        detail: str, steps: Sequence[str], current_step: int,
    ) -> None:
        async with self._lock:
            if not self._owns(task_id, worker_id):
                return
            task = self._tasks[task_id]
            task.phase, task.progress, task.detail = phase, progress, detail
            task.steps = _steps_for(task_id, steps, current_step, detail)
            task.updated_at = datetime.now(timezone.utc)

    async def renew(self, task_id: UUID, worker_id: str, lease_seconds: int) -> bool:
        async with self._lock:
            if not self._owns(task_id, worker_id):
                return False
            self._leases[task_id] = (
                worker_id,
                datetime.now(timezone.utc) + timedelta(seconds=lease_seconds),
            )
            return True

    async def complete(self, task_id: UUID, worker_id: str, outcome: TaskOutcome) -> None:
        async with self._lock:
            if not self._owns(task_id, worker_id):
                return
            task = self._tasks[task_id]
            now = datetime.now(timezone.utc)
            task.status, task.phase, task.progress = "completed", "completed", 1
            task.detail, task.result_summary, task.updated_at = outcome.summary, outcome.summary, now
            task.steps = [step.model_copy(update={"status": "completed"}) for step in task.steps]
            task.artifacts = [
                ServerTaskArtifact(
                    id=uuid4(), taskId=task_id, kind=item.kind, title=item.title,
                    contentType=item.content_type, payload=item.payload,
                    storageReference=item.storage_reference, createdAt=now,
                ) for item in outcome.artifacts
            ]
            self._leases.pop(task_id, None)

    async def fail(
        self, task_id: UUID, worker_id: str, message: str, *, retry: bool,
        retry_delay_seconds: int,
    ) -> None:
        _ = retry_delay_seconds
        async with self._lock:
            if not self._owns(task_id, worker_id):
                return
            task = self._tasks[task_id]
            task.status = "queued" if retry else "failed"
            task.phase = "retrying" if retry else "failed"
            task.detail = "等待重试" if retry else message
            task.error_message = message
            task.updated_at = datetime.now(timezone.utc)
            self._leases.pop(task_id, None)

    async def close(self) -> None:
        pass


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


ProgressReporter = Callable[[str, float, str, Sequence[str], int], Awaitable[None]]


class ServerTaskHandler(Protocol):
    async def run(self, task: ServerTask, report: ProgressReporter) -> TaskOutcome: ...


class ServerTaskRunner:
    def __init__(
        self, store: ServerTaskStore, handlers: Sequence[ServerTaskHandler], *,
        worker_id: str, lease_seconds: int = 300, max_attempts: int = 3,
    ) -> None:
        self._store = store
        self._handlers: dict[str, ServerTaskHandler] = {}
        for handler in handlers:
            capabilities = getattr(handler, "capabilities", None)
            if capabilities is None:
                capability = getattr(handler, "capability", None)
                capabilities = (capability,) if isinstance(capability, str) else ()
            for capability in capabilities:
                if capability in self._handlers:
                    raise ValueError(f"Duplicate server task capability: {capability}")
                self._handlers[capability] = handler
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    async def run_once(self) -> bool:
        task = await self._store.claim(self._worker_id, self._lease_seconds)
        if task is None:
            return False
        handler = self._handlers.get(task.capability)
        if handler is None:
            await self._store.fail(task.id, self._worker_id, "没有可执行该任务的服务端 Handler。", retry=False, retry_delay_seconds=0)
            return True

        async def report(phase: str, progress: float, detail: str, steps: Sequence[str], current_step: int) -> None:
            await self._store.update_progress(
                task.id, self._worker_id, phase=phase, progress=progress,
                detail=detail, steps=steps, current_step=current_step,
            )

        try:
            heartbeat = asyncio.create_task(self._heartbeat(task.id))
            try:
                outcome = await handler.run(task, report)
                await self._store.complete(task.id, self._worker_id, outcome)
            finally:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
        except asyncio.CancelledError:
            raise
        except Exception as error:
            retry = task.attempt_count < self._max_attempts
            await self._store.fail(
                task.id, self._worker_id, str(error), retry=retry,
                retry_delay_seconds=min(2 ** task.attempt_count, 30),
            )
        return True

    async def _heartbeat(self, task_id: UUID) -> None:
        interval = max(self._lease_seconds / 3, 1)
        while True:
            await asyncio.sleep(interval)
            if not await self._store.renew(
                task_id, self._worker_id, self._lease_seconds
            ):
                return


def _steps_for(task_id: UUID, titles: Sequence[str], current: int, detail: str) -> list[ServerTaskStep]:
    return [ServerTaskStep(
        id=uuid4(), taskId=task_id, order=index, title=title,
        status="completed" if index < current else "running" if index == current else "pending",
        detail=detail if index == current else None,
    ) for index, title in enumerate(titles)]
