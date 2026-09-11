import json

import httpx
import pytest

from app.config import Settings
from app.protocol import ChatRequest
from app.providers.qwen import QwenProvider, UpstreamError


def make_request() -> ChatRequest:
    return ChatRequest.model_validate({
        "requestId": "req_123",
        "protocolVersion": "1.0",
        "messages": [{"role": "user", "content": "提醒我提交报销"}],
        "deviceContext": {
            "locale": "zh-CN",
            "timeZone": "Asia/Shanghai",
            "capabilitySetVersion": "ios-v1",
        },
    })


def make_settings() -> Settings:
    return Settings(
        api_key="secret-test-key",
        base_url="https://workspace.example.com/compatible-mode/v1/",
        model="qwen-multimodal-test",
    )


@pytest.mark.anyio
async def test_normalizes_qwen_stream_to_wellphone_protocol() -> None:
    captured_request: httpx.Request | None = None
    upstream = (
        'data: {"choices":[{"delta":{"content":"好的"}}]}\n\n'
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
        '"function":{"name":"reminder_create","arguments":"{\\"title\\":\\"提交报销\\",'
        '\\"dueAt\\":\\"2099-09-11T15:00:00+08:00\\"}"}}]}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=upstream,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = QwenProvider(make_settings(), client=client)
    stream = await provider.open_reply(make_request())
    encoded = "".join([event async for event in stream.events()])
    events = [
        json.loads(line.removeprefix("data: "))
        for line in encoded.splitlines()
        if line.startswith("data: ")
    ]

    assert captured_request is not None
    assert str(captured_request.url) == (
        "https://workspace.example.com/compatible-mode/v1/chat/completions"
    )
    upstream_body = json.loads(captured_request.content)
    assert upstream_body["model"] == "qwen-multimodal-test"
    assert upstream_body["tools"][0]["function"]["name"] == "reminder_create"
    assert "Asia/Shanghai" in upstream_body["messages"][0]["content"]

    assert [event["type"] for event in events] == [
        "response.started",
        "assistant.delta",
        "tool.requested",
        "response.completed",
    ]
    assert events[2]["capability"] == "reminder.create"
    assert events[2]["arguments"]["title"] == "提交报销"
    assert "reminder_create" not in events[2]
    await client.aclose()


@pytest.mark.anyio
async def test_maps_upstream_failure_before_starting_client_stream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content="bad credentials")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = QwenProvider(make_settings(), client=client)

    with pytest.raises(UpstreamError) as captured:
        await provider.open_reply(make_request())

    assert captured.value.status == 401
    await client.aclose()


@pytest.mark.anyio
async def test_maps_transport_failure_to_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = QwenProvider(make_settings(), client=client)

    with pytest.raises(UpstreamError) as captured:
        await provider.open_reply(make_request())

    assert captured.value.status == 502
    await client.aclose()
