"""Storage contract for checkpoints reported by device-executed tasks."""

from typing import Protocol
from uuid import UUID

from app.api.protocol import TaskCheckpointSubmission
from app.tasks.checkpoints.models import TaskCheckpointRecord


class TaskCheckpointStore(Protocol):
    async def initialize(self) -> None: ...
    async def is_healthy(self) -> bool: ...
    async def record(
        self,
        conversation_id: UUID,
        checkpoint: TaskCheckpointSubmission,
    ) -> TaskCheckpointRecord: ...
    async def close(self) -> None: ...
