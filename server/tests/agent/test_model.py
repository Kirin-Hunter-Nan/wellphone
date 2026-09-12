"""Qwen Agent Loop model-boundary resilience tests."""

import json

import httpx
import pytest

from app.agent.model import QwenAgentLoopModel
from app.core.config import Settings


@pytest.mark.anyio
async def test_malformed_tool_arguments_become_a_retryable_tool_call() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_malformed",
                        "type": "function",
                        "function": {
                            "name": "itinerary_submit",
                            "arguments": '{"plan":{"title":"上海旅行" "days":[]}}',
                        },
                    }],
                },
            }],
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = QwenAgentLoopModel(
        Settings(
            api_key="test-key",
            base_url="https://example.com/compatible-mode/v1/",
            model="qwen-test",
        ),
        client=client,
    )

    try:
        turn = await model.next_turn([], [])
    finally:
        await client.aclose()

    assert len(turn.tool_calls) == 1
    call = turn.tool_calls[0]
    assert call.arguments == {}
    assert call.argument_error is not None
    assert "not valid JSON" in call.argument_error
    normalized = turn.message["tool_calls"][0]["function"]["arguments"]
    assert json.loads(normalized) == {}
