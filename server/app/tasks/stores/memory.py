"""In-memory server-task store used by tests and local composition."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.tasks.models import (
    DeviceToolCall,
    DeviceToolConflictError,
    ServerTask,
    ServerTaskArtifact,
    ServerTaskCreate,
    TaskOutcome,
    TaskStatus,
)
from app.tasks.steps import _steps_for

class InMemoryServerTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[UUID, ServerTask] = {}
        self._leases: dict[UUID, tuple[str, datetime]] = {}
        self._device_tools: dict[tuple[UUID, str], DeviceToolCall] = {}
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

    async def enqueue_device_tool(
        self, task_id: UUID, tool_call_id: str, tool_name: str,
        arguments: dict[str, object],
    ) -> DeviceToolCall:
        async with self._lock:
            if task_id not in self._tasks:
                raise KeyError(f"Task {task_id} does not exist")
            key = (task_id, tool_call_id)
            existing = self._device_tools.get(key)
            if existing is not None:
                if existing.tool_name != tool_name or existing.arguments != arguments:
                    raise DeviceToolConflictError(
                        "Device Tool call id identifies different arguments"
                    )
                return existing.model_copy(deep=True)
            call = DeviceToolCall(
                taskId=task_id,
                toolCallId=tool_call_id,
                toolName=tool_name,
                arguments=arguments,
                status="pending",
                requestedAt=datetime.now(timezone.utc),
            )
            self._device_tools[key] = call
            return call.model_copy(deep=True)

    async def get_pending_device_tool(self, task_id: UUID) -> DeviceToolCall | None:
        pending = sorted(
            (
                call for call in self._device_tools.values()
                if call.task_id == task_id and call.status == "pending"
            ),
            key=lambda call: call.requested_at,
        )
        return pending[0].model_copy(deep=True) if pending else None

    async def get_device_tool(
        self, task_id: UUID, tool_call_id: str,
    ) -> DeviceToolCall | None:
        call = self._device_tools.get((task_id, tool_call_id))
        return call.model_copy(deep=True) if call else None

    async def submit_device_tool_result(
        self, task_id: UUID, tool_call_id: str, *,
        result: dict[str, object] | None,
        error_code: str | None,
        error_message: str | None,
    ) -> DeviceToolCall | None:
        async with self._lock:
            call = self._device_tools.get((task_id, tool_call_id))
            if call is None:
                return None
            desired_status = "completed" if result is not None else "failed"
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
                return call.model_copy(deep=True)
            call.status = desired_status
            call.result = result
            call.error_code = error_code
            call.error_message = error_message
            call.completed_at = datetime.now(timezone.utc)
            return call.model_copy(deep=True)

    async def close(self) -> None:
        pass
