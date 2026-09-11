from uuid import UUID

import pytest

from app.postgres_store import InMemoryToolResultStore, ToolResultConflictError
from app.protocol import ToolResultSubmission
from app.providers.base import ProviderToolCallContext


def submission(*, summary: str = "已验证") -> ToolResultSubmission:
    return ToolResultSubmission.model_validate({
        "requestId": "result_123",
        "protocolVersion": "1.0",
        "toolCallId": "call_123",
        "taskId": "7c215f3c-e513-49cc-b645-20dfbb1aa954",
        "capability": "reminder.create",
        "status": "verified",
        "result": {"summary": summary},
    })


@pytest.mark.anyio
async def test_records_tool_results_idempotently(anyio_backend) -> None:
    del anyio_backend
    store = InMemoryToolResultStore()
    conversation_id = UUID("39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7")

    await store.initialize()
    assert await store.record(conversation_id, submission()) is True
    assert await store.record(conversation_id, submission()) is False
    with pytest.raises(ToolResultConflictError):
        await store.record(conversation_id, submission(summary="不同结果"))
    await store.close()


@pytest.mark.anyio
async def test_stores_provider_context_and_assistant_reply_idempotently(
    anyio_backend,
) -> None:
    del anyio_backend
    store = InMemoryToolResultStore()
    conversation_id = UUID("39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7")
    context = ProviderToolCallContext(
        tool_call_id="call_123",
        capability="reminder.create",
        provider="qwen",
        state={"messages": [{"role": "user", "content": "提醒我"}]},
    )

    await store.save_tool_call_context(conversation_id, context)
    await store.save_tool_call_context(conversation_id, context)
    assert await store.get_tool_call_context(conversation_id, "call_123") == context
    assert await store.get_assistant_reply(conversation_id, "call_123") is None

    first_claim = await store.claim_continuation(
        conversation_id,
        "call_123",
        lease_seconds=150,
    )
    concurrent_claim = await store.claim_continuation(
        conversation_id,
        "call_123",
        lease_seconds=150,
    )
    assert first_claim.status == "claimed"
    assert concurrent_claim.status == "processing"

    await store.fail_continuation(conversation_id, "call_123", "temporary_error")
    retry_claim = await store.claim_continuation(
        conversation_id,
        "call_123",
        lease_seconds=150,
    )
    assert retry_claim.status == "claimed"

    await store.save_assistant_reply(conversation_id, "call_123", "已经创建。")
    await store.save_assistant_reply(conversation_id, "call_123", "已经创建。")
    assert await store.get_assistant_reply(conversation_id, "call_123") == "已经创建。"
    completed_claim = await store.claim_continuation(
        conversation_id,
        "call_123",
        lease_seconds=150,
    )
    assert completed_claim.status == "completed"
    assert completed_claim.assistant_reply == "已经创建。"
    with pytest.raises(ToolResultConflictError):
        await store.save_assistant_reply(conversation_id, "call_123", "不同回复")
