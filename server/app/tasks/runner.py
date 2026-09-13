"""Worker runner for claimed long-running server tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from typing import Protocol
from uuid import UUID

from app.tasks.models import ServerTask, TaskOutcome
from app.tasks.store import ServerTaskStore

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
            execution = asyncio.create_task(handler.run(task, report))
            heartbeat = asyncio.create_task(self._heartbeat(task.id))
            try:
                done, _ = await asyncio.wait(
                    (execution, heartbeat),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if heartbeat in done and not execution.done():
                    heartbeat.result()
                    execution.cancel()
                    with suppress(asyncio.CancelledError):
                        await execution
                    return True
                outcome = await execution
                await self._store.complete(task.id, self._worker_id, outcome)
            finally:
                if not execution.done():
                    execution.cancel()
                    with suppress(asyncio.CancelledError):
                        await execution
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
        except asyncio.CancelledError:
            raise
        except Exception as error:
            retry = (
                getattr(error, "retryable", True)
                and task.attempt_count < self._max_attempts
            )
            await self._store.fail(
                task.id, self._worker_id, str(error), retry=retry,
                retry_delay_seconds=min(2 ** task.attempt_count, 30),
            )
        return True

    async def _heartbeat(self, task_id: UUID) -> None:
        interval = min(max(self._lease_seconds / 3, 0.05), 2)
        while True:
            await asyncio.sleep(interval)
            if not await self._store.renew(
                task_id, self._worker_id, self._lease_seconds
            ):
                return
