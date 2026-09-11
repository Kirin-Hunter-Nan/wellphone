import json

import httpx
import pytest

from app.config import Settings
from app.protocol import ChatRequest, ToolResultSubmission
from app.providers.base import ProviderToolCallContext
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
    captured_contexts: list[ProviderToolCallContext] = []
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
    async def capture_context(context: ProviderToolCallContext) -> None:
        captured_contexts.append(context)

    stream = await provider.open_reply(make_request(), on_tool_call=capture_context)
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
    assert len(captured_contexts) == 1
    assert captured_contexts[0].tool_call_id == "call_1"
    assert captured_contexts[0].capability == "reminder.create"
    assert captured_contexts[0].provider == "qwen"
    assert captured_contexts[0].state["toolName"] == "reminder_create"
    await client.aclose()


@pytest.mark.anyio
async def test_continues_qwen_with_the_verified_device_tool_result() -> None:
    captured_body: dict[str, object] | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_body
        captured_body = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "提醒已经创建。"}}]
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = QwenProvider(make_settings(), client=client)
    context = ProviderToolCallContext(
        tool_call_id="call_1",
        capability="reminder.create",
        provider="qwen",
        state={
            "toolName": "reminder_create",
            "messages": [
                {"role": "user", "content": "提醒我提交报销"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "reminder_create",
                            "arguments": "{\"title\":\"提交报销\"}",
                        },
                    }],
                },
            ],
        },
    )
    result = ToolResultSubmission.model_validate({
        "requestId": "result_1",
        "protocolVersion": "1.0",
        "toolCallId": "call_1",
        "taskId": "7c215f3c-e513-49cc-b645-20dfbb1aa954",
        "capability": "reminder.create",
        "status": "verified",
        "result": {"summary": "提醒事项已创建并验证。"},
    })

    reply = await provider.continue_reply(context, result)

    assert reply == "提醒已经创建。"
    assert captured_body is not None
    assert captured_body["stream"] is False
    assert captured_body["tool_choice"] == "none"
    assert captured_body["tools"][0]["function"]["name"] == "reminder_create"
    tool_message = captured_body["messages"][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_1"
    assert json.loads(tool_message["content"])["status"] == "verified"
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
