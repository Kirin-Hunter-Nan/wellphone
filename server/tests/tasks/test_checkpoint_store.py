"""Device task checkpoint persistence tests."""

from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.api.protocol import TaskCheckpointSubmission
from app.tasks.checkpoint_store import InMemoryTaskCheckpointStore, TaskCheckpointConflictError


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


@pytest.mark.anyio
async def test_out_of_order_checkpoint_is_stored_but_does_not_roll_back_snapshot(
    anyio_backend,
) -> None:
    del anyio_backend
    store = InMemoryTaskCheckpointStore()
    conversation_id = UUID("69fcd9e0-e03e-4ed9-a22e-b92079c15a44")
    latest = checkpoint(revision=3, status="completed", phase="completed")
    stale = checkpoint(revision=2, status="running", phase="verifying")

    third = await store.record(conversation_id, latest)
    second = await store.record(conversation_id, stale)
    duplicate_second = await store.record(conversation_id, stale)

    assert third.applied is True
    assert third.current_revision == 3
    assert third.gap is True
    assert second.duplicate is False
    assert second.applied is False
    assert second.current_revision == 3
    assert second.gap is False
    assert duplicate_second.duplicate is True
    assert duplicate_second.applied is False
    assert duplicate_second.current_revision == 3


@pytest.mark.anyio
async def test_request_id_cannot_be_reused_for_another_revision(anyio_backend) -> None:
    del anyio_backend
    store = InMemoryTaskCheckpointStore()
    conversation_id = UUID("79fcd9e0-e03e-4ed9-a22e-b92079c15a55")
    await store.record(
        conversation_id,
        checkpoint(
            revision=1,
            status="waitingForConfirmation",
            phase="waitingForConfirmation",
            request_id="stable-request-id",
        ),
    )

    with pytest.raises(TaskCheckpointConflictError):
        await store.record(
            conversation_id,
            checkpoint(
                revision=2,
                status="running",
                phase="executing",
                request_id="stable-request-id",
            ),
        )


@pytest.mark.anyio
async def test_same_tool_call_identity_is_scoped_to_conversation(anyio_backend) -> None:
    del anyio_backend
    store = InMemoryTaskCheckpointStore()
    first_conversation = UUID("89fcd9e0-e03e-4ed9-a22e-b92079c15a66")
    second_conversation = UUID("99fcd9e0-e03e-4ed9-a22e-b92079c15a77")

    first = await store.record(
        first_conversation,
        checkpoint(revision=1, status="running", phase="executing"),
    )
    second = await store.record(
        second_conversation,
        checkpoint(
            revision=1,
            status="running",
            phase="executing",
            task_id="8c215f3c-e513-49cc-b645-20dfbb1aa955",
        ),
    )

    assert first.applied is True
    assert second.applied is True
