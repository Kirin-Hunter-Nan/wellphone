from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.protocol import TaskCheckpointSubmission
from app.task_store import InMemoryTaskCheckpointStore, TaskCheckpointConflictError


def checkpoint(
    *,
    revision: int,
    phase: str,
    status: str,
    request_id: str | None = None,
    task_id: str = "7c215f3c-e513-49cc-b645-20dfbb1aa954",
    tool_call_id: str = "call_123",
) -> TaskCheckpointSubmission:
    return TaskCheckpointSubmission.model_validate({
        "requestId": request_id or f"checkpoint-{revision}",
        "protocolVersion": "1.0",
        "taskId": task_id,
        "revision": revision,
        "toolCallId": tool_call_id,
        "capability": "reminder.create",
        "status": status,
        "phase": phase,
        "progress": 0.5,
        "occurredAt": datetime.now(timezone.utc).isoformat(),
    })


@pytest.mark.anyio
async def test_records_idempotent_monotonic_task_checkpoints(anyio_backend) -> None:
    del anyio_backend
    store = InMemoryTaskCheckpointStore()
    conversation_id = UUID("39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7")
    first = checkpoint(revision=1, status="waitingForConfirmation", phase="waitingForConfirmation")
    third = checkpoint(revision=3, status="completed", phase="completed")

    recorded = await store.record(conversation_id, first)
    duplicate = await store.record(conversation_id, first)
    jumped = await store.record(conversation_id, third)

    assert recorded.applied is True
    assert recorded.gap is False
    assert duplicate.duplicate is True
    assert duplicate.applied is False
    assert jumped.applied is True
    assert jumped.current_revision == 3
    assert jumped.gap is True


@pytest.mark.anyio
async def test_rejects_checkpoint_revision_with_different_content(anyio_backend) -> None:
    del anyio_backend
    store = InMemoryTaskCheckpointStore()
    conversation_id = UUID("49fcd9e0-e03e-4ed9-a22e-b92079c15a22")
    await store.record(
        conversation_id,
        checkpoint(revision=1, status="waitingForConfirmation", phase="waitingForConfirmation"),
    )

    with pytest.raises(TaskCheckpointConflictError):
        await store.record(
            conversation_id,
            checkpoint(revision=1, status="running", phase="executing"),
        )


@pytest.mark.anyio
async def test_rejects_tool_call_reassigned_to_another_task(anyio_backend) -> None:
    del anyio_backend
    store = InMemoryTaskCheckpointStore()
    conversation_id = UUID("59fcd9e0-e03e-4ed9-a22e-b92079c15a33")
    await store.record(
        conversation_id,
        checkpoint(revision=1, status="running", phase="executing"),
    )

    with pytest.raises(TaskCheckpointConflictError):
        await store.record(
            conversation_id,
            checkpoint(
                revision=1,
                status="running",
                phase="executing",
                request_id="checkpoint-other-task",
                task_id="8c215f3c-e513-49cc-b645-20dfbb1aa955",
            ),
        )
