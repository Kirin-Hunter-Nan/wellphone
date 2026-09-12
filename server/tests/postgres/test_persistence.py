"""Production PostgreSQL adapter contracts against an isolated test database."""

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.agent.journal import PostgreSQLAgentLoopJournal
from app.api.protocol import ChatRequest, TaskCheckpointSubmission, ToolResultSubmission
from app.conversations.stores.postgres import PostgreSQLChatRequestStore
from app.providers.base import ProviderToolCallContext
from app.tasks.checkpoints.stores.postgres import PostgreSQLTaskCheckpointStore
from app.tasks.models import ArtifactDraft, ServerTaskCreate, TaskOutcome
from app.tasks.stores.postgres import PostgreSQLServerTaskStore
from app.tool_results.stores.postgres import PostgreSQLToolResultStore


@pytest.fixture
def postgres_url() -> str:
    value = os.environ.get("WELLPHONE_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Set WELLPHONE_TEST_DATABASE_URL to run PostgreSQL contracts")
    return value


async def test_server_task_and_loop_journal_survive_store_reopen(
    anyio_backend, postgres_url: str,
) -> None:
    store = PostgreSQLServerTaskStore(postgres_url)
    await store.initialize()
    conversation_id = uuid4()
    created = await store.create(conversation_id, ServerTaskCreate(
        capability="test.postgres",
        title="Postgres task",
        input={"value": 1},
        requiresConfirmation=True,
        toolCallId=f"call-{uuid4()}",
    ))
    await store.confirm(created.id)
    claimed = await store.claim("worker-a", 60)
    assert claimed is not None and claimed.id == created.id
    await store.update_progress(
        created.id, "worker-a", phase="executing", progress=0.6,
        detail="working", steps=("work", "save"), current_step=0,
    )
    await store.complete(created.id, "worker-a", TaskOutcome(
        summary="persisted",
        artifacts=(ArtifactDraft(
            kind="json", title="result", content_type="application/json",
            payload={"ok": True},
        ),),
    ))

    journal = PostgreSQLAgentLoopJournal(postgres_url)
    await journal.initialize()
    first = await journal.append(created.id, "model.turn", {"index": 1})
    second = await journal.append(created.id, "tool.result", {"index": 2})
    await journal.close()
    await store.close()

    reopened_store = PostgreSQLServerTaskStore(postgres_url)
    reopened_journal = PostgreSQLAgentLoopJournal(postgres_url)
    await reopened_store.initialize()
    await reopened_journal.initialize()
    restored = await reopened_store.get(created.id)
    events = await reopened_journal.load(created.id)

    assert restored is not None
    assert restored.status == "completed"
    assert restored.result_summary == "persisted"
    assert restored.artifacts[0].payload == {"ok": True}
    assert (first.sequence, second.sequence) == (1, 2)
    assert [event.event_type for event in events] == ["model.turn", "tool.result"]
    await reopened_journal.close()
    await reopened_store.close()


async def test_checkpoint_idempotency_and_monotonic_snapshot_use_real_constraints(
    anyio_backend, postgres_url: str,
) -> None:
    store = PostgreSQLTaskCheckpointStore(postgres_url)
    await store.initialize()
    conversation_id, task_id = uuid4(), uuid4()

    def checkpoint(revision: int, status: str, phase: str):
        return TaskCheckpointSubmission.model_validate({
            "requestId": f"checkpoint-{task_id}-{revision}",
            "protocolVersion": "1.0",
            "taskId": str(task_id),
            "revision": revision,
            "toolCallId": f"call-{task_id}",
            "capability": "reminder.create",
            "status": status,
            "phase": phase,
            "progress": 1 if status == "completed" else 0.5,
            "occurredAt": datetime.now(timezone.utc).isoformat(),
        })

    latest = checkpoint(3, "completed", "completed")
    stale = checkpoint(2, "running", "verifying")
    inserted = await store.record(conversation_id, latest)
    duplicate = await store.record(conversation_id, latest)
    out_of_order = await store.record(conversation_id, stale)

    assert inserted.applied is True and inserted.current_revision == 3
    assert duplicate.duplicate is True and duplicate.applied is False
    assert out_of_order.duplicate is False and out_of_order.applied is False
    assert out_of_order.current_revision == 3
    await store.close()


async def test_chat_request_replay_and_context_survive_store_reopen(
    anyio_backend, postgres_url: str,
) -> None:
    conversation_id = uuid4()
    request = ChatRequest.model_validate({
        "requestId": f"request-{uuid4()}",
        "protocolVersion": "1.0",
        "messages": [{"role": "user", "content": "第一问"}],
        "deviceContext": {
            "locale": "zh-CN", "timeZone": "Asia/Shanghai",
            "capabilitySetVersion": "ios-v1",
        },
    })
    store = PostgreSQLChatRequestStore(postgres_url)
    await store.initialize()
    claim = await store.claim(conversation_id, request, lease_seconds=60)
    assert claim.status == "claimed"
    await store.complete(
        conversation_id, request, ("data: one\n\n",), "服务端回复"
    )
    await store.close()

    reopened = PostgreSQLChatRequestStore(postgres_url)
    await reopened.initialize()
    replay = await reopened.claim(conversation_id, request, lease_seconds=60)
    follow_up = request.model_copy(update={
        "request_id": f"request-{uuid4()}",
        "messages": [
            *request.messages,
            request.messages[0].model_copy(update={"content": "第二问"}),
        ],
    })
    follow_up_claim = await reopened.claim(
        conversation_id, follow_up, lease_seconds=60
    )
    contextualized = await reopened.contextualize(
        conversation_id, follow_up, max_messages=10, max_characters=1_000
    )

    assert replay.status == "completed"
    assert follow_up_claim.status == "claimed"
    assert replay.events == ("data: one\n\n",)
    assert [message.content for message in contextualized.messages] == [
        "第一问", "服务端回复", "第二问"
    ]
    await reopened.close()


async def test_tool_result_context_and_continuation_survive_store_reopen(
    anyio_backend, postgres_url: str,
) -> None:
    conversation_id = uuid4()
    tool_call_id = f"call-{uuid4()}"
    context = ProviderToolCallContext(
        tool_call_id=tool_call_id,
        capability="reminder.create",
        provider="qwen",
        state={"messages": [], "toolName": "reminder_create"},
    )
    submission = ToolResultSubmission.model_validate({
        "requestId": f"result-{uuid4()}",
        "protocolVersion": "1.0",
        "toolCallId": tool_call_id,
        "taskId": str(uuid4()),
        "capability": "reminder.create",
        "status": "verified",
        "result": {"summary": "created"},
    })
    store = PostgreSQLToolResultStore(postgres_url)
    await store.initialize()
    await store.save_tool_call_context(conversation_id, context)
    assert await store.record(conversation_id, submission) is True
    assert (await store.claim_continuation(
        conversation_id, tool_call_id, lease_seconds=60
    )).status == "claimed"
    await store.save_assistant_reply(conversation_id, tool_call_id, "已经创建。")
    await store.close()

    reopened = PostgreSQLToolResultStore(postgres_url)
    await reopened.initialize()
    restored_context = await reopened.get_tool_call_context(
        conversation_id, tool_call_id
    )
    restored_claim = await reopened.claim_continuation(
        conversation_id, tool_call_id, lease_seconds=60
    )

    assert restored_context == context
    assert restored_claim.status == "completed"
    assert restored_claim.assistant_reply == "已经创建。"
    assert await reopened.record(conversation_id, submission) is False
    await reopened.close()
