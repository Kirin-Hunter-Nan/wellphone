"""Server task lease, retry, cancellation, and idempotency regression tests."""

import asyncio
from uuid import uuid4

import pytest

from app.tasks.models import ArtifactDraft, ServerTask, ServerTaskCreate, TaskOutcome
from app.tasks.runner import ServerTaskRunner
from app.tasks.stores.memory import InMemoryServerTaskStore


class EventuallySuccessfulHandler:
    capability = "test.eventual"

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def run(self, task: ServerTask, report) -> TaskOutcome:
        self.calls += 1
        await report(
            "executing", 0.5, f"attempt {self.calls}", ("execute", "persist"), 0
        )
        if self.calls <= self.failures:
            raise RuntimeError(f"transient failure {self.calls}")
        return TaskOutcome(
            summary="recovered successfully",
            artifacts=(ArtifactDraft(
                kind="json",
                title="attempts",
                content_type="application/json",
                payload={"attempts": self.calls},
            ),),
        )


class NeverCalledHandler:
    capability = "test.cancelled"

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, task: ServerTask, report) -> TaskOutcome:
        self.calls += 1
        return TaskOutcome(summary="unexpected")


class BlockingHandler:
    capability = "test.blocking"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def run(self, task: ServerTask, report) -> TaskOutcome:
        self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


async def create_task(
    store: InMemoryServerTaskStore,
    capability: str,
    *,
    conversation_id=None,
    tool_call_id: str | None = None,
    requires_confirmation: bool = False,
):
    return await store.create(
        conversation_id or uuid4(),
        ServerTaskCreate(
            capability=capability,
            title="Resilience test",
            input={"value": 1},
            toolCallId=tool_call_id,
            requiresConfirmation=requires_confirmation,
        ),
    )


async def test_transient_failures_retry_then_persist_one_successful_outcome(
    anyio_backend,
) -> None:
    store = InMemoryServerTaskStore()
    created = await create_task(store, "test.eventual")
    handler = EventuallySuccessfulHandler(failures=2)
    runner = ServerTaskRunner(
        store, [handler], worker_id="worker-a", max_attempts=3
    )

    assert await runner.run_once() is True
    first = await store.get(created.id)
    assert first is not None
    assert (first.status, first.phase, first.attempt_count) == ("queued", "retrying", 1)

    assert await runner.run_once() is True
    second = await store.get(created.id)
    assert second is not None
    assert (second.status, second.phase, second.attempt_count) == ("queued", "retrying", 2)

    assert await runner.run_once() is True
    completed = await store.get(created.id)
    assert completed is not None
    assert (completed.status, completed.phase, completed.attempt_count) == (
        "completed", "completed", 3
    )
    assert completed.result_summary == "recovered successfully"
    assert len(completed.artifacts) == 1
    assert completed.artifacts[0].payload == {"attempts": 3}
    assert handler.calls == 3
    assert await runner.run_once() is False


async def test_expired_lease_is_reclaimed_and_stale_worker_cannot_commit(
    anyio_backend,
) -> None:
    store = InMemoryServerTaskStore()
    created = await create_task(store, "test.lease")

    first_claim = await store.claim("worker-a", lease_seconds=0)
    second_claim = await store.claim("worker-b", lease_seconds=60)

    assert first_claim is not None and first_claim.id == created.id
    assert second_claim is not None and second_claim.id == created.id
    assert second_claim.attempt_count == 2

    await store.complete(
        created.id, "worker-a", TaskOutcome(summary="stale result")
    )
    after_stale_commit = await store.get(created.id)
    assert after_stale_commit is not None
    assert after_stale_commit.status == "running"
    assert after_stale_commit.result_summary is None

    await store.complete(
        created.id, "worker-b", TaskOutcome(summary="authoritative result")
    )
    completed = await store.get(created.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result_summary == "authoritative result"


async def test_cancelled_task_is_never_claimed_or_executed(anyio_backend) -> None:
    store = InMemoryServerTaskStore()
    created = await create_task(store, "test.cancelled")
    cancelled = await store.cancel(created.id)
    handler = NeverCalledHandler()

    handled = await ServerTaskRunner(
        store, [handler], worker_id="worker"
    ).run_once()

    assert cancelled is not None and cancelled.status == "cancelled"
    assert handled is False
    assert handler.calls == 0


async def test_running_task_cancellation_releases_worker_promptly(anyio_backend) -> None:
    store = InMemoryServerTaskStore()
    created = await create_task(store, "test.blocking")
    handler = BlockingHandler()
    runner = ServerTaskRunner(
        store,
        [handler],
        worker_id="worker",
        lease_seconds=0.15,
    )

    run = asyncio.create_task(runner.run_once())
    await handler.started.wait()
    await store.cancel(created.id)

    assert await asyncio.wait_for(run, timeout=1) is True
    assert handler.cancelled.is_set()
    cancelled = await store.get(created.id)
    assert cancelled is not None and cancelled.status == "cancelled"


async def test_tool_call_id_is_idempotent_within_conversation_only(
    anyio_backend,
) -> None:
    store = InMemoryServerTaskStore()
    conversation_a = uuid4()
    conversation_b = uuid4()

    original = await create_task(
        store,
        "test.idempotent",
        conversation_id=conversation_a,
        tool_call_id="call-1",
    )
    duplicate = await create_task(
        store,
        "test.idempotent",
        conversation_id=conversation_a,
        tool_call_id="call-1",
    )
    other_conversation = await create_task(
        store,
        "test.idempotent",
        conversation_id=conversation_b,
        tool_call_id="call-1",
    )

    assert duplicate.id == original.id
    assert other_conversation.id != original.id
    assert len(await store.list(conversation_a)) == 1
    assert len(await store.list(conversation_b)) == 1


async def test_unknown_capability_fails_once_without_retry(anyio_backend) -> None:
    store = InMemoryServerTaskStore()
    created = await create_task(store, "test.unknown")
    runner = ServerTaskRunner(store, [], worker_id="worker", max_attempts=5)

    assert await runner.run_once() is True
    failed = await store.get(created.id)

    assert failed is not None
    assert failed.status == "failed"
    assert failed.attempt_count == 1
    assert "没有可执行" in (failed.error_message or "")
    assert await runner.run_once() is False


def test_duplicate_handler_capability_is_rejected() -> None:
    first = EventuallySuccessfulHandler(failures=0)
    second = EventuallySuccessfulHandler(failures=0)

    with pytest.raises(ValueError, match="Duplicate server task capability"):
        ServerTaskRunner(
            InMemoryServerTaskStore(), [first, second], worker_id="worker"
        )
