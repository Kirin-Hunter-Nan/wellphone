from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.postgres_store import InMemoryToolResultStore
from app.protocol import ChatRequest, assistant_delta, encode_sse, response_completed


class FakeStream:
    async def events(self) -> AsyncIterator[str]:
        yield encode_sse(assistant_delta("resp_test", "你好"))
        yield encode_sse(response_completed("resp_test"))


class FakeProvider:
    request: ChatRequest | None = None

    async def open_reply(self, request: ChatRequest) -> FakeStream:
        self.request = request
        return FakeStream()

    async def close(self) -> None:
        pass


class UnhealthyResultStore(InMemoryToolResultStore):
    async def is_healthy(self) -> bool:
        return False


def settings() -> Settings:
    return Settings(
        api_key="test-key",
        base_url="https://workspace.example.com/compatible-mode/v1/",
        model="qwen-test",
    )


def test_health_and_message_stream() -> None:
    provider = FakeProvider()
    app = create_app(
        settings=settings(),
        provider=provider,
        result_store=InMemoryToolResultStore(),
    )

    with TestClient(app) as client:
        health = client.get("/health")
        response = client.post(
            "/v1/conversations/39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7/messages",
            json={
                "requestId": "req_123",
                "protocolVersion": "1.0",
                "messages": [{"role": "user", "content": "你好"}],
                "deviceContext": {
                    "locale": "zh-CN",
                    "timeZone": "Asia/Shanghai",
                    "capabilitySetVersion": "ios-v1",
                },
            },
            headers={"accept": "text/event-stream"},
        )

    assert health.json() == {
        "status": "ok",
        "model": "qwen-test",
        "protocolVersion": "1.0",
    }
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"type":"assistant.delta"' in response.text
    assert provider.request is not None


def test_rejects_legacy_request_without_protocol_context() -> None:
    app = create_app(
        settings=settings(),
        provider=FakeProvider(),
        result_store=InMemoryToolResultStore(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/conversations/39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7/messages",
            json={"messages": [{"role": "user", "content": "你好"}]},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_health_fails_when_postgres_is_unavailable() -> None:
    app = create_app(
        settings=settings(),
        provider=FakeProvider(),
        result_store=UnhealthyResultStore(),
    )
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"


def test_accepts_tool_results_idempotently_and_rejects_conflicts() -> None:
    app = create_app(
        settings=settings(),
        provider=FakeProvider(),
        result_store=InMemoryToolResultStore(),
    )
    endpoint = (
        "/v1/conversations/39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7/tool-results"
    )
    payload = {
        "requestId": "result_123",
        "protocolVersion": "1.0",
        "toolCallId": "call_123",
        "taskId": "7c215f3c-e513-49cc-b645-20dfbb1aa954",
        "capability": "reminder.create",
        "status": "verified",
        "result": {"summary": "提醒事项已创建并验证。"},
    }

    with TestClient(app) as client:
        first = client.post(endpoint, json=payload)
        duplicate = client.post(endpoint, json={**payload, "requestId": "result_retry"})
        conflict = client.post(endpoint, json={**payload, "status": "declined", "result": None})

    assert first.status_code == 200
    assert first.json() == {
        "accepted": True,
        "duplicate": False,
        "protocolVersion": "1.0",
    }
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "tool_result_conflict"


def test_rejects_invalid_tool_result_outcome() -> None:
    app = create_app(
        settings=settings(),
        provider=FakeProvider(),
        result_store=InMemoryToolResultStore(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/conversations/39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7/tool-results",
            json={
                "requestId": "result_123",
                "protocolVersion": "1.0",
                "toolCallId": "call_123",
                "taskId": "7c215f3c-e513-49cc-b645-20dfbb1aa954",
                "capability": "reminder.create",
                "status": "failed",
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
