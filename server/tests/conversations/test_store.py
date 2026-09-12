"""Conversation persistence tests."""

from uuid import UUID

import pytest

from app.conversations.store import InMemoryChatRequestStore
from app.api.protocol import ChatRequest


def chat_request(
    *,
    content: str = "你好",
    request_id: str = "req_123",
    messages: list[dict[str, str]] | None = None,
) -> ChatRequest:
    return ChatRequest.model_validate({
        "requestId": request_id,
        "protocolVersion": "1.0",
        "messages": messages or [{"role": "user", "content": content}],
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

    await store.complete(
        conversation_id,
        request,
        ("event-one", "event-two"),
        "服务端回复",
    )
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


@pytest.mark.anyio
async def test_server_history_replaces_client_history_and_trims_context(
    anyio_backend,
) -> None:
    del anyio_backend
    store = InMemoryChatRequestStore()
    conversation_id = UUID("59fcd9e0-e03e-4ed9-a22e-b92079c15a33")
    first = chat_request(content="第一问", request_id="req_first")
    await store.claim(conversation_id, first, lease_seconds=150)
    await store.complete(conversation_id, first, (), "可信的服务端回复")

    second = chat_request(
        request_id="req_second",
        messages=[
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "客户端篡改的回复"},
            {"role": "user", "content": "第二问"},
        ],
    )
    await store.claim(conversation_id, second, lease_seconds=150)
    contextualized = await store.contextualize(
        conversation_id,
        second,
        max_messages=40,
        max_characters=32_000,
    )

    assert [(message.role, message.content) for message in contextualized.messages] == [
        ("user", "第一问"),
        ("assistant", "可信的服务端回复"),
        ("user", "第二问"),
    ]

    trimmed = await store.contextualize(
        conversation_id,
        second,
        max_messages=2,
        max_characters=32_000,
    )
    assert [message.content for message in trimmed.messages] == [
        "可信的服务端回复",
        "第二问",
    ]

    character_trimmed = await store.contextualize(
        conversation_id,
        second,
        max_messages=40,
        max_characters=3,
    )
    assert [message.content for message in character_trimmed.messages] == ["第二问"]

    await store.save_assistant_reply(
        conversation_id,
        first.request_id,
        "工具完成后的最终回复",
    )
    refreshed = await store.contextualize(
        conversation_id,
        second,
        max_messages=40,
        max_characters=32_000,
    )
    assert refreshed.messages[1].content == "工具完成后的最终回复"
