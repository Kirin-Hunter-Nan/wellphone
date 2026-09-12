"""In-memory checkpoint store for tests and local dependency injection."""

from uuid import UUID

from app.api.protocol import TaskCheckpointSubmission
from app.tasks.checkpoints.canonical import checkpoint_fingerprint
from app.tasks.checkpoints.models import (
    TaskCheckpointConflictError,
    TaskCheckpointRecord,
)


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
        fingerprint = checkpoint_fingerprint(checkpoint)
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
