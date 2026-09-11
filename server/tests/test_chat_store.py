from uuid import UUID

import pytest

from app.chat_store import InMemoryChatRequestStore
from app.protocol import ChatRequest


def chat_request(*, content: str = "你好") -> ChatRequest:
    return ChatRequest.model_validate({
        "requestId": "req_123",
        "protocolVersion": "1.0",
        "messages": [{"role": "user", "content": content}],
        "deviceContext": {
            "locale": "zh-CN",
            "timeZone": "Asia/Shanghai",
            "capabilitySetVersion": "ios-v1",
        },
    })


@pytest.mark.anyio
async def test_chat_request_claims_replays_and_rejects_conflicts(
    anyio_backend,
) -> None:
    del anyio_backend
    store = InMemoryChatRequestStore()
    conversation_id = UUID("39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7")
    request = chat_request()

    first = await store.claim(conversation_id, request, lease_seconds=150)
    concurrent = await store.claim(conversation_id, request, lease_seconds=150)
    assert first.status == "claimed"
    assert concurrent.status == "processing"

    await store.complete(conversation_id, request, ("event-one", "event-two"))
    replay = await store.claim(conversation_id, request, lease_seconds=150)
    conflict = await store.claim(
        conversation_id,
        chat_request(content="不同内容"),
        lease_seconds=150,
    )
    assert replay.status == "completed"
    assert replay.events == ("event-one", "event-two")
    assert conflict.status == "conflict"


@pytest.mark.anyio
async def test_failed_chat_request_can_be_claimed_again(anyio_backend) -> None:
    del anyio_backend
    store = InMemoryChatRequestStore()
    conversation_id = UUID("49fcd9e0-e03e-4ed9-a22e-b92079c15a22")
    request = chat_request()

    assert (await store.claim(conversation_id, request, lease_seconds=150)).status == "claimed"
    await store.fail(conversation_id, request.request_id, "upstream_error")
    assert (await store.claim(conversation_id, request, lease_seconds=150)).status == "claimed"
