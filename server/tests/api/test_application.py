"""API application integration tests."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi.testclient import TestClient

from app.conversations.store import InMemoryChatRequestStore
from app.core.config import Settings
from app.api.application import create_app
from app.api.support import deterministic_tool_followup, finish_store_operation
from app.tool_results.store import InMemoryToolResultStore
from app.api.protocol import (
    ChatRequest,
    ToolResultSubmission,
    assistant_delta,
    encode_sse,
    response_completed,
)
from app.providers.base import ProviderError, ProviderToolCallContext, ToolCallContextSink


class FakeStream:
    async def events(self) -> AsyncIterator[str]:
        yield encode_sse(assistant_delta("resp_test", "你好"))
        yield encode_sse(response_completed("resp_test"))


def test_travel_tool_followup_points_to_task_detail_without_claiming_files() -> None:
    submission = ToolResultSubmission.model_validate({
        "requestId": "result_travel",
        "protocolVersion": "1.0",
        "toolCallId": "call_travel",
        "taskId": "39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7",
        "capability": "travel.plan",
        "status": "verified",
        "result": {
            "summary": "上海三日行程已完成。",
            "artifacts": [{
                "id": "cc37e242-41ec-4d10-a730-f69605483c24",
                "kind": "text",
                "title": "上海三日行程（文本版）",
                "contentType": "text/markdown",
                "payload": "# 上海三日行程\n\n## 第一天\n\n- 上海博物馆",
            }],
        },
    })

    reply = deterministic_tool_followup(submission)

    assert reply is not None
    assert reply.startswith("# 上海三日行程\n\n上海三日行程已完成。")
    assert "# 上海三日行程" in reply
    assert "上海博物馆" in reply
    assert "文件" not in reply


def test_business_trip_followup_includes_the_verified_text_artifact() -> None:
    submission = ToolResultSubmission.model_validate({
        "requestId": "result_business_trip",
        "protocolVersion": "1.0",
        "toolCallId": "call_business_trip",
        "taskId": "39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7",
        "capability": "business-trip.plan",
        "status": "verified",
        "result": {
            "summary": "上海商务出差计划已完成，未发现时间冲突。",
            "artifacts": [{
                "id": "cc37e242-41ec-4d10-a730-f69605483c24",
                "kind": "text",
                "title": "上海商务出差计划（文本版）",
                "contentType": "text/markdown",
                "payload": "# 上海商务出差计划\n\n- 14:00 客户方案会",
                "storageReference": "https://drive.google.com/file/d/file-1/view",
            }],
        },
    })

    reply = deterministic_tool_followup(submission)

    assert reply is not None
    assert reply.startswith(
        "# 上海商务出差计划\n\n"
        "上海商务出差计划已完成，未发现时间冲突。"
    )
    assert "上海商务出差计划已完成" in reply
    assert "客户方案会" in reply
    assert "\n\n## 文件\n\n" in reply
    assert "在 Google Drive 中打开已上传的 PDF" in reply
    assert "https://drive.google.com/file/d/file-1/view" in reply


class FakeProvider:
    def __init__(self) -> None:
        self.request: ChatRequest | None = None
        self.requests: list[ChatRequest] = []
        self.open_count = 0
        self.continuation_count = 0

    async def open_reply(
        self,
        request: ChatRequest,
        *,
        on_tool_call: ToolCallContextSink | None = None,
    ) -> FakeStream:
        del on_tool_call
        self.request = request
        self.requests.append(request)
        self.open_count += 1
        return FakeStream()

    async def continue_reply(
        self,
        context: ProviderToolCallContext,
        result: ToolResultSubmission,
    ) -> str:
        self.continuation_count += 1
        assert context.tool_call_id == result.tool_call_id
        return "提醒事项已经成功创建。"

    async def close(self) -> None:
        pass


class FailOnceContinuationProvider(FakeProvider):
    async def continue_reply(
        self,
        context: ProviderToolCallContext,
        result: ToolResultSubmission,
    ) -> str:
        self.continuation_count += 1
        if self.continuation_count == 1:
            raise ProviderError(502, "temporary failure")
        assert context.tool_call_id == result.tool_call_id
        return "提醒事项已经成功创建。"


class FailOnceOpenProvider(FakeProvider):
    async def open_reply(
        self,
        request: ChatRequest,
        *,
        on_tool_call: ToolCallContextSink | None = None,
    ) -> FakeStream:
        del on_tool_call
        self.request = request
        self.requests.append(request)
        self.open_count += 1
        if self.open_count == 1:
            raise ProviderError(502, "temporary failure")
        return FakeStream()


class ToolContextProvider(FakeProvider):
    async def open_reply(
        self,
        request: ChatRequest,
        *,
        on_tool_call: ToolCallContextSink | None = None,
    ) -> FakeStream:
        self.request = request
        self.requests.append(request)
        self.open_count += 1
        if self.open_count == 1 and on_tool_call is not None:
            await on_tool_call(ProviderToolCallContext(
                tool_call_id="call_history",
                capability="reminder.create",
                provider="fake",
                state={"messages": [], "toolName": "reminder_create"},
            ))
        return FakeStream()


class UnhealthyResultStore(InMemoryToolResultStore):
    async def is_healthy(self) -> bool:
        return False


def settings() -> Settings:
    return Settings(
        api_key="test-key",
        base_url="https://workspace.example.com/compatible-mode/v1/",
        model="qwen-test",
    )


def test_server_task_lifecycle_endpoints() -> None:
    app = create_app(
        settings=settings(),
        provider=FakeProvider(),
        result_store=InMemoryToolResultStore(),
    )
    conversation_id = "39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7"

    with TestClient(app) as client:
        created = client.post(
            f"/v1/conversations/{conversation_id}/tasks",
            json={
                "capability": "system.long-task-probe",
                "title": "验证后台任务",
                "input": {"message": "hello"},
                "requiresConfirmation": True,
            },
        )
        task_id = created.json()["id"]
        listed = client.get(f"/v1/conversations/{conversation_id}/tasks")
        confirmed = client.post(f"/v1/tasks/{task_id}/confirm")
        fetched = client.get(f"/v1/tasks/{task_id}")

    assert created.status_code == 201
    assert created.json()["status"] == "waitingForConfirmation"
    assert listed.json()["tasks"][0]["id"] == task_id
    assert confirmed.json()["status"] == "queued"
    assert fetched.json()["status"] == "queued"


def test_phone_can_claim_and_complete_a_pending_device_tool() -> None:
    from app.tasks.stores.memory import InMemoryServerTaskStore

    task_store = InMemoryServerTaskStore()
    app = create_app(
        settings=settings(),
        provider=FakeProvider(),
        result_store=InMemoryToolResultStore(),
        server_task_store=task_store,
    )
    conversation_id = "39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7"

    with TestClient(app) as client:
        created = client.post(
            f"/v1/conversations/{conversation_id}/tasks",
            json={
                "capability": "travel.plan",
                "title": "规划上海旅行",
                "input": {},
            },
        ).json()
        task_id = UUID(created["id"])
        asyncio.run(task_store.enqueue_device_tool(
            task_id,
            "search_1",
            "mapkit.local-search",
            {"query": "上海博物馆", "destination": "上海"},
        ))
        pending = client.get(f"/v1/tasks/{task_id}/device-tools/pending")
        result = client.post(
            f"/v1/tasks/{task_id}/device-tools/results",
            json={
                "requestId": "result_search_1",
                "protocolVersion": "1.0",
                "toolCallId": "search_1",
                "status": "completed",
                "result": {
                    "name": "上海博物馆（人民广场馆）",
                    "map_url": "https://maps.apple.com/place?place-id=test",
                    "verified": True,
                    "source": "mapkit-native",
                },
            },
        )
        empty = client.get(f"/v1/tasks/{task_id}/device-tools/pending")

    assert pending.status_code == 200
    assert pending.json()["toolName"] == "mapkit.local-search"
    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert empty.status_code == 204


def test_failed_device_tool_submission_cannot_smuggle_a_completed_result() -> None:
    app = create_app(
        settings=settings(),
        provider=FakeProvider(),
        result_store=InMemoryToolResultStore(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/tasks/39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7/device-tools/results",
            json={
                "requestId": "invalid_failed_result",
                "protocolVersion": "1.0",
                "toolCallId": "search_1",
                "status": "failed",
                "result": {"verified": True},
                "error": {"code": "mapkit_failed", "message": "failed"},
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


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


def test_replays_completed_chat_request_without_calling_provider_again() -> None:
    provider = FakeProvider()
    app = create_app(
        settings=settings(),
        provider=provider,
        result_store=InMemoryToolResultStore(),
    )
    endpoint = "/v1/conversations/39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7/messages"
    payload = {
        "requestId": "req_replay",
        "protocolVersion": "1.0",
        "messages": [{"role": "user", "content": "你好"}],
        "deviceContext": {
            "locale": "zh-CN",
            "timeZone": "Asia/Shanghai",
            "capabilitySetVersion": "ios-v1",
        },
    }

    with TestClient(app) as client:
        first = client.post(endpoint, json=payload)
        replay = client.post(endpoint, json=payload)
        conflict = client.post(
            endpoint,
            json={
                **payload,
                "messages": [{"role": "user", "content": "不同内容"}],
            },
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.text == first.text
    assert replay.headers["x-wellphone-replayed"] == "true"
    assert provider.open_count == 1
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "chat_request_conflict"


def test_provider_uses_server_owned_conversation_history() -> None:
    provider = FakeProvider()
    chat_store = InMemoryChatRequestStore()
    app = create_app(
        settings=settings(),
        provider=provider,
        result_store=InMemoryToolResultStore(),
        chat_request_store=chat_store,
    )
    endpoint = "/v1/conversations/69fcd9e0-e03e-4ed9-a22e-b92079c15a44/messages"
    context = {
        "locale": "zh-CN",
        "timeZone": "Asia/Shanghai",
        "capabilitySetVersion": "ios-v1",
    }

    with TestClient(app) as client:
        first = client.post(endpoint, json={
            "requestId": "req_history_first",
            "protocolVersion": "1.0",
            "messages": [{"role": "user", "content": "第一问"}],
            "deviceContext": context,
        })
        second = client.post(endpoint, json={
            "requestId": "req_history_second",
            "protocolVersion": "1.0",
            "messages": [
                {"role": "user", "content": "第一问"},
                {"role": "assistant", "content": "客户端篡改的历史"},
                {"role": "user", "content": "第二问"},
            ],
            "deviceContext": context,
        })

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(provider.requests) == 2
    assert [message.content for message in provider.requests[1].messages] == [
        "第一问",
        "你好",
        "第二问",
    ]


def test_tool_continuation_becomes_authoritative_assistant_history() -> None:
    provider = ToolContextProvider()
    result_store = InMemoryToolResultStore()
    chat_store = InMemoryChatRequestStore()
    app = create_app(
        settings=settings(),
        provider=provider,
        result_store=result_store,
        chat_request_store=chat_store,
    )
    conversation_id = "79fcd9e0-e03e-4ed9-a22e-b92079c15a55"
    message_endpoint = f"/v1/conversations/{conversation_id}/messages"
    result_endpoint = f"/v1/conversations/{conversation_id}/tool-results"
    context = {
        "locale": "zh-CN",
        "timeZone": "Asia/Shanghai",
        "capabilitySetVersion": "ios-v1",
    }

    with TestClient(app) as client:
        first = client.post(message_endpoint, json={
            "requestId": "req_tool_history",
            "protocolVersion": "1.0",
            "messages": [{"role": "user", "content": "提醒我"}],
            "deviceContext": context,
        })
        result = client.post(result_endpoint, json={
            "requestId": "result_tool_history",
            "protocolVersion": "1.0",
            "toolCallId": "call_history",
            "taskId": "7c215f3c-e513-49cc-b645-20dfbb1aa954",
            "capability": "reminder.create",
            "status": "verified",
            "result": {"summary": "提醒事项已创建并验证。"},
        })
        second = client.post(message_endpoint, json={
            "requestId": "req_after_tool",
            "protocolVersion": "1.0",
            "messages": [
                {"role": "user", "content": "提醒我"},
                {"role": "assistant", "content": "客户端旧占位回复"},
                {"role": "user", "content": "刚才完成了吗？"},
            ],
            "deviceContext": context,
        })

    assert first.status_code == 200
    assert result.status_code == 200
    assert second.status_code == 200
    assert [message.content for message in provider.requests[1].messages] == [
        "提醒我",
        "提醒事项已经成功创建。",
        "刚才完成了吗？",
    ]


def test_failed_chat_request_releases_claim_for_same_request_id_retry() -> None:
    provider = FailOnceOpenProvider()
    app = create_app(
        settings=settings(),
        provider=provider,
        result_store=InMemoryToolResultStore(),
    )
    endpoint = "/v1/conversations/49fcd9e0-e03e-4ed9-a22e-b92079c15a22/messages"
    payload = {
        "requestId": "req_retry",
        "protocolVersion": "1.0",
        "messages": [{"role": "user", "content": "你好"}],
        "deviceContext": {
            "locale": "zh-CN",
            "timeZone": "Asia/Shanghai",
            "capabilitySetVersion": "ios-v1",
        },
    }

    with TestClient(app) as client:
        failed = client.post(endpoint, json=payload)
        retried = client.post(endpoint, json=payload)

    assert failed.status_code == 502
    assert retried.status_code == 200
    assert provider.open_count == 2


def test_rejects_duplicate_chat_request_while_lease_is_active() -> None:
    conversation_id = UUID("59fcd9e0-e03e-4ed9-a22e-b92079c15a33")
    chat_store = InMemoryChatRequestStore()
    request = ChatRequest.model_validate({
        "requestId": "req_processing",
        "protocolVersion": "1.0",
        "messages": [{"role": "user", "content": "你好"}],
        "deviceContext": {
            "locale": "zh-CN",
            "timeZone": "Asia/Shanghai",
            "capabilitySetVersion": "ios-v1",
        },
    })
    asyncio.run(chat_store.claim(conversation_id, request, lease_seconds=150))
    app = create_app(
        settings=settings(),
        provider=FakeProvider(),
        result_store=InMemoryToolResultStore(),
        chat_request_store=chat_store,
    )

    with TestClient(app) as client:
        response = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json=request.model_dump(mode="json", by_alias=True),
        )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    assert response.json()["error"]["code"] == "chat_request_in_progress"


def test_persistence_cleanup_survives_request_cancellation() -> None:
    cleanup_completed = False

    async def cleanup() -> None:
        nonlocal cleanup_completed
        await asyncio.sleep(0)
        cleanup_completed = True

    async def cancel_during_cleanup() -> None:
        current_task = asyncio.current_task()
        assert current_task is not None
        asyncio.get_running_loop().call_soon(current_task.cancel)
        await finish_store_operation(cleanup())
        await asyncio.sleep(0)

    asyncio.run(cancel_during_cleanup())
    assert cleanup_completed is True


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
        "continuationStatus": "unavailable",
        "protocolVersion": "1.0",
    }
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "tool_result_conflict"


def test_continues_model_from_saved_tool_call_and_reuses_reply_on_retry() -> None:
    provider = FakeProvider()
    store = InMemoryToolResultStore()
    conversation_id = "39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7"
    asyncio.run(store.save_tool_call_context(
        UUID(conversation_id),
        ProviderToolCallContext(
            tool_call_id="call_123",
            capability="reminder.create",
            provider="fake",
            state={"messages": []},
        ),
    ))
    app = create_app(settings=settings(), provider=provider, result_store=store)
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
        first = client.post(f"/v1/conversations/{conversation_id}/tool-results", json=payload)
        retry = client.post(
            f"/v1/conversations/{conversation_id}/tool-results",
            json={**payload, "requestId": "result_retry"},
        )

    assert first.status_code == 200
    assert first.json()["continuationStatus"] == "completed"
    assert first.json()["assistantMessage"] == "提醒事项已经成功创建。"
    assert retry.json()["assistantMessage"] == first.json()["assistantMessage"]
    assert retry.json()["duplicate"] is True
    assert provider.continuation_count == 1


def test_failed_model_continuation_releases_claim_for_retry() -> None:
    provider = FailOnceContinuationProvider()
    store = InMemoryToolResultStore()
    conversation_id = UUID("49fcd9e0-e03e-4ed9-a22e-b92079c15a22")
    asyncio.run(store.save_tool_call_context(
        conversation_id,
        ProviderToolCallContext(
            tool_call_id="call_retry",
            capability="reminder.create",
            provider="fake",
            state={"messages": []},
        ),
    ))
    app = create_app(settings=settings(), provider=provider, result_store=store)
    payload = {
        "requestId": "result_retry_1",
        "protocolVersion": "1.0",
        "toolCallId": "call_retry",
        "taskId": "8c215f3c-e513-49cc-b645-20dfbb1aa955",
        "capability": "reminder.create",
        "status": "verified",
        "result": {"summary": "提醒事项已创建并验证。"},
    }
    endpoint = f"/v1/conversations/{conversation_id}/tool-results"

    with TestClient(app) as client:
        failed = client.post(endpoint, json=payload)
        retried = client.post(endpoint, json={**payload, "requestId": "result_retry_2"})

    assert failed.status_code == 502
    assert retried.status_code == 200
    assert retried.json()["continuationStatus"] == "completed"
    assert provider.continuation_count == 2


def test_rejects_a_second_continuation_while_the_database_lease_is_active() -> None:
    store = InMemoryToolResultStore()
    conversation_id = UUID("59fcd9e0-e03e-4ed9-a22e-b92079c15a33")
    asyncio.run(store.save_tool_call_context(
        conversation_id,
        ProviderToolCallContext(
            tool_call_id="call_processing",
            capability="reminder.create",
            provider="fake",
            state={"messages": []},
        ),
    ))
    asyncio.run(store.claim_continuation(
        conversation_id,
        "call_processing",
        lease_seconds=150,
    ))
    app = create_app(
        settings=settings(),
        provider=FakeProvider(),
        result_store=store,
    )

    with TestClient(app) as client:
        response = client.post(
            f"/v1/conversations/{conversation_id}/tool-results",
            json={
                "requestId": "result_processing",
                "protocolVersion": "1.0",
                "toolCallId": "call_processing",
                "taskId": "9c215f3c-e513-49cc-b645-20dfbb1aa956",
                "capability": "reminder.create",
                "status": "declined",
            },
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "continuation_in_progress"


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


def test_accepts_task_checkpoints_idempotently_and_tracks_revision_gaps() -> None:
    app = create_app(
        settings=settings(),
        provider=FakeProvider(),
        result_store=InMemoryToolResultStore(),
    )
    endpoint = (
        "/v1/conversations/39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7/task-checkpoints"
    )
    payload = {
        "requestId": "checkpoint_1",
        "protocolVersion": "1.0",
        "taskId": "7c215f3c-e513-49cc-b645-20dfbb1aa954",
        "revision": 1,
        "toolCallId": "call_123",
        "capability": "reminder.create",
        "status": "waitingForConfirmation",
        "phase": "waitingForConfirmation",
        "progress": 0.35,
        "detail": "等待用户确认",
        "executionAttemptCount": 0,
        "occurredAt": "2026-09-11T08:00:00Z",
    }

    with TestClient(app) as client:
        first = client.post(endpoint, json=payload)
        duplicate = client.post(endpoint, json=payload)
        jumped = client.post(endpoint, json={
            **payload,
            "requestId": "checkpoint_3",
            "revision": 3,
            "status": "completed",
            "phase": "completed",
            "progress": 1,
        })
        conflict = client.post(endpoint, json={
            **payload,
            "status": "running",
            "phase": "executing",
        })

    assert first.json()["applied"] is True
    assert duplicate.json()["duplicate"] is True
    assert jumped.json()["currentRevision"] == 3
    assert jumped.json()["gap"] is True
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "task_checkpoint_conflict"


def test_rejects_retry_time_outside_running_execution() -> None:
    app = create_app(
        settings=settings(),
        provider=FakeProvider(),
        result_store=InMemoryToolResultStore(),
    )
    endpoint = (
        "/v1/conversations/39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7/task-checkpoints"
    )

    with TestClient(app) as client:
        response = client.post(endpoint, json={
            "requestId": "checkpoint_invalid_retry",
            "protocolVersion": "1.0",
            "taskId": "7c215f3c-e513-49cc-b645-20dfbb1aa954",
            "revision": 1,
            "toolCallId": "call_123",
            "capability": "reminder.create",
            "status": "completed",
            "phase": "completed",
            "progress": 1,
            "executionAttemptCount": 1,
            "nextExecutionRetryAt": "2026-09-11T08:01:00Z",
            "occurredAt": "2026-09-11T08:00:00Z",
        })

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_accepts_persisted_task_cancellation_request() -> None:
    app = create_app(
        settings=settings(),
        provider=FakeProvider(),
        result_store=InMemoryToolResultStore(),
    )
    endpoint = (
        "/v1/conversations/39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7/task-checkpoints"
    )

    with TestClient(app) as client:
        response = client.post(endpoint, json={
            "requestId": "checkpoint_cancellation_requested",
            "protocolVersion": "1.0",
            "taskId": "7c215f3c-e513-49cc-b645-20dfbb1aa954",
            "revision": 2,
            "toolCallId": "call_cancel",
            "capability": "reminder.create",
            "status": "running",
            "phase": "executing",
            "progress": 0.55,
            "executionAttemptCount": 1,
            "cancellationRequestedAt": "2026-09-11T08:00:30Z",
            "occurredAt": "2026-09-11T08:00:30Z",
        })

    assert response.status_code == 200
    assert response.json()["applied"] is True


def test_accepts_active_execution_deadline() -> None:
    app = create_app(
        settings=settings(),
        provider=FakeProvider(),
        result_store=InMemoryToolResultStore(),
    )
    endpoint = (
        "/v1/conversations/39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7/task-checkpoints"
    )

    with TestClient(app) as client:
        response = client.post(endpoint, json={
            "requestId": "checkpoint_execution_deadline",
            "protocolVersion": "1.0",
            "taskId": "6ea0b625-4c52-49f0-8b0b-98cfefc34cf3",
            "revision": 2,
            "toolCallId": "call_deadline",
            "capability": "reminder.create",
            "status": "running",
            "phase": "executing",
            "progress": 0.55,
            "executionAttemptCount": 1,
            "executionDeadlineAt": "2026-09-11T08:01:00Z",
            "occurredAt": "2026-09-11T08:00:30Z",
        })

    assert response.status_code == 200
    assert response.json()["applied"] is True
