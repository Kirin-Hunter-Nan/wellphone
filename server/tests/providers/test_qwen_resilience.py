"""Qwen streaming edge cases and continuation validation."""

import json

import httpx
import pytest

from app.api.protocol import ChatRequest, ToolResultSubmission
from app.core.config import Settings
from app.providers.base import ProviderToolCallContext
from app.providers.qwen import QwenProvider, UpstreamError


def settings() -> Settings:
    return Settings(
        api_key="test-key",
        base_url="https://workspace.example.com/compatible-mode/v1/",
        model="qwen-test",
    )


def chat_request() -> ChatRequest:
    return ChatRequest.model_validate({
        "requestId": "request-1",
        "protocolVersion": "1.0",
        "messages": [{"role": "user", "content": "提醒我提交材料"}],
        "deviceContext": {
            "locale": "zh-CN",
            "timeZone": "Asia/Shanghai",
            "capabilitySetVersion": "ios-v1",
        },
    })


def result_submission() -> ToolResultSubmission:
    return ToolResultSubmission.model_validate({
        "requestId": "result-1",
        "protocolVersion": "1.0",
        "toolCallId": "call-1",
        "taskId": "7c215f3c-e513-49cc-b645-20dfbb1aa954",
        "capability": "reminder.create",
        "status": "verified",
        "result": {"summary": "created"},
    })


def stream_client(upstream: str, *, content_type: str = "text/event-stream"):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": content_type}, content=upstream
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def decoded_events(provider: QwenProvider) -> list[dict[str, object]]:
    stream = await provider.open_reply(chat_request())
    encoded = "".join([event async for event in stream.events()])
    return [
        json.loads(line.removeprefix("data: "))
        for line in encoded.splitlines()
        if line.startswith("data: ")
    ]


async def test_fragmented_tool_arguments_are_accumulated_exactly_once(
    anyio_backend,
) -> None:
    upstream = (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1",'
        '"function":{"name":"reminder_create","arguments":"{\\"title\\":\\""}}]}}]}\n\n'
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        '"function":{"arguments":"提交材料\\",\\"dueAt\\":\\"2099-01-02T09:00:00+08:00\\"}"}}]}}]}\n\n'
        "data: [DONE]\n\n"
    )
    client = stream_client(upstream)

    events = await decoded_events(QwenProvider(settings(), client=client))

    assert [event["type"] for event in events] == [
        "response.started", "tool.requested", "response.completed"
    ]
    assert events[1]["toolCallId"] == "call-1"
    assert events[1]["arguments"]["title"] == "提交材料"
    await client.aclose()


async def test_parallel_tool_calls_fail_without_emitting_partial_requests(
    anyio_backend,
) -> None:
    upstream = (
        'data: {"choices":[{"delta":{"tool_calls":['
        '{"index":0,"id":"call-1","function":{"name":"reminder_create",'
        '"arguments":"{\\"title\\":\\"A\\",\\"dueAt\\":\\"2099-01-02T09:00:00+08:00\\"}"}},'
        '{"index":1,"id":"call-2","function":{"name":"reminder_create",'
        '"arguments":"{\\"title\\":\\"B\\",\\"dueAt\\":\\"2099-01-03T09:00:00+08:00\\"}"}}]}}]}\n\n'
        "data: [DONE]\n\n"
    )
    client = stream_client(upstream)

    events = await decoded_events(QwenProvider(settings(), client=client))

    assert [event["type"] for event in events] == [
        "response.started", "response.failed"
    ]
    assert events[-1]["error"]["code"] == "parallel_tool_calls_unsupported"
    await client.aclose()


@pytest.mark.parametrize(
    ("upstream", "expected_code"),
    [
        ("data: not-json\n\n", "invalid_upstream_event"),
        ("data: []\n\n", "invalid_upstream_event"),
        (
            'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
            "upstream_stream_interrupted",
        ),
    ],
)
async def test_invalid_or_interrupted_streams_end_with_a_protocol_failure(
    anyio_backend, upstream: str, expected_code: str,
) -> None:
    client = stream_client(upstream)

    events = await decoded_events(QwenProvider(settings(), client=client))

    assert events[-1]["type"] == "response.failed"
    assert events[-1]["error"]["code"] == expected_code
    assert all(event["type"] != "response.completed" for event in events)
    await client.aclose()


async def test_unknown_model_tool_is_rejected_at_protocol_boundary(
    anyio_backend,
) -> None:
    upstream = (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1",'
        '"function":{"name":"dangerous_unknown_tool","arguments":"{}"}}]}}]}\n\n'
        "data: [DONE]\n\n"
    )
    client = stream_client(upstream)

    events = await decoded_events(QwenProvider(settings(), client=client))

    assert events[-1]["type"] == "response.failed"
    assert events[-1]["error"]["code"] == "invalid_tool_request"
    await client.aclose()


async def test_non_streaming_upstream_response_is_rejected(anyio_backend) -> None:
    client = stream_client("{}", content_type="application/json")
    provider = QwenProvider(settings(), client=client)

    with pytest.raises(UpstreamError, match="non-streaming") as captured:
        await provider.open_reply(chat_request())

    assert captured.value.status == 502
    await client.aclose()


async def test_continuation_rejects_wrong_provider_before_network_call(
    anyio_backend,
) -> None:
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = QwenProvider(settings(), client=client)
    context = ProviderToolCallContext(
        tool_call_id="call-1",
        capability="reminder.create",
        provider="another-provider",
        state={"toolName": "reminder_create", "messages": []},
    )

    with pytest.raises(UpstreamError, match="another provider") as captured:
        await provider.continue_reply(context, result_submission())

    assert captured.value.status == 500
    assert requests == 0
    await client.aclose()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
    ],
)
async def test_invalid_or_empty_continuation_is_rejected(
    anyio_backend, payload: dict[str, object],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = QwenProvider(settings(), client=client)
    context = ProviderToolCallContext(
        tool_call_id="call-1",
        capability="reminder.create",
        provider="qwen",
        state={"toolName": "reminder_create", "messages": []},
    )

    with pytest.raises(UpstreamError):
        await provider.continue_reply(context, result_submission())

    await client.aclose()
