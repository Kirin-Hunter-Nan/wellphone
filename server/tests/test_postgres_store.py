from uuid import UUID

import pytest

from app.postgres_store import InMemoryToolResultStore, ToolResultConflictError
from app.protocol import ToolResultSubmission


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
