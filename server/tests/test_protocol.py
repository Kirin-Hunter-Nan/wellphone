import json

import pytest
from pydantic import ValidationError

from app.protocol import (
    AttachmentReference,
    ChatRequest,
    TaskArtifact,
    TaskCheckpointSubmission,
    ToolResultSubmission,
    assistant_delta,
    encode_sse,
)


def valid_request() -> dict[str, object]:
    return {
        "requestId": "req_123",
        "protocolVersion": "1.0",
        "messages": [{"role": "user", "content": "你好"}],
        "deviceContext": {
            "locale": "zh-CN",
            "timeZone": "Asia/Shanghai",
            "capabilitySetVersion": "ios-v1",
        },
    }


def test_accepts_text_and_multimodal_messages() -> None:
    request = valid_request()
    request["messages"] = [
        {"role": "assistant", "content": "请上传票据"},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64,AA=="},
                },
                {"type": "text", "text": "识别这张票据"},
            ],
        },
    ]

    parsed = ChatRequest.model_validate(request)
    assert len(parsed.messages) == 2
    assert parsed.device_context.time_zone == "Asia/Shanghai"


def test_validates_attachment_references_and_task_artifacts() -> None:
    attachment = AttachmentReference.model_validate({
        "id": "cc37e242-41ec-4d10-a730-f69605483c24",
        "kind": "image",
        "mimeType": "image/jpeg",
        "byteCount": 1024,
        "width": 800,
        "height": 600,
        "storageReference": "attachments/cc37e242.jpg",
    })
    artifact = TaskArtifact.model_validate({
        "id": "0ba3420c-b499-4c30-8130-f054cf5b51de",
        "taskId": "395dd81c-a5bd-4906-9586-42d3c3dd80f5",
        "kind": "itinerary",
        "title": "上海三日行程",
        "contentType": "application/vnd.wellphone.itinerary+json",
        "payload": {"days": []},
        "createdAt": "2026-09-11T03:00:00Z",
    })

    assert attachment.mime_type == "image/jpeg"
    assert artifact.kind == "itinerary"


def test_rejects_unknown_protocol_and_assistant_final_message() -> None:
    request = valid_request()
    request["protocolVersion"] = "2.0"
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(request)

    request = valid_request()
    request["messages"] = [{"role": "assistant", "content": "完成"}]
    with pytest.raises(ValidationError, match="final message"):
        ChatRequest.model_validate(request)


def test_encodes_provider_neutral_sse() -> None:
    encoded = encode_sse(assistant_delta("resp_123", "你好"))
    payload = json.loads(encoded.removeprefix("data: ").strip())

    assert payload == {
        "type": "assistant.delta",
        "protocolVersion": "1.0",
        "responseId": "resp_123",
        "text": "你好",
    }


def test_validates_tool_result_outcomes() -> None:
    verified = ToolResultSubmission.model_validate({
        "requestId": "result_123",
        "protocolVersion": "1.0",
        "toolCallId": "call_123",
        "taskId": "39e6cc7c-2b6f-4a2c-a34d-ed2e996fe2e7",
        "capability": "reminder.create",
        "status": "verified",
        "result": {"summary": "提醒事项已创建并验证。"},
    })
    assert verified.result is not None

    invalid = verified.model_dump(mode="json", by_alias=True)
    invalid["status"] = "failed"
    invalid.pop("result")
    with pytest.raises(ValidationError, match="requires error data"):
        ToolResultSubmission.model_validate(invalid)


def test_checkpoint_fingerprint_keeps_legacy_zero_attempts_compatible() -> None:
    payload = {
        "requestId": "checkpoint_1",
        "protocolVersion": "1.0",
        "taskId": "7c215f3c-e513-49cc-b645-20dfbb1aa954",
        "revision": 1,
        "toolCallId": "call_123",
        "capability": "reminder.create",
        "status": "running",
        "phase": "executing",
        "occurredAt": "2026-09-11T08:00:00Z",
    }
    legacy = TaskCheckpointSubmission.model_validate(payload)
    explicit_zero = TaskCheckpointSubmission.model_validate({
        **payload,
        "executionAttemptCount": 0,
    })

    assert legacy.semantic_payload() == explicit_zero.semantic_payload()
    assert "executionAttemptCount" not in legacy.semantic_payload()

    retrying = TaskCheckpointSubmission.model_validate({
        **payload,
        "executionAttemptCount": 1,
        "nextExecutionRetryAt": "2026-09-11T08:01:00Z",
        "lastExecutionErrorMessage": "temporary failure",
    })
    assert retrying.semantic_payload()["executionAttemptCount"] == 1

    stopping = TaskCheckpointSubmission.model_validate({
        **payload,
        "executionAttemptCount": 1,
        "cancellationRequestedAt": "2026-09-11T08:00:30Z",
    })
    assert stopping.semantic_payload()["cancellationRequestedAt"] == (
        "2026-09-11T08:00:30Z"
    )

    executing = TaskCheckpointSubmission.model_validate({
        **payload,
        "executionAttemptCount": 1,
        "executionDeadlineAt": "2026-09-11T08:01:00Z",
    })
    assert executing.semantic_payload()["executionDeadlineAt"] == (
        "2026-09-11T08:01:00Z"
    )

    with pytest.raises(ValidationError, match="active execution attempt"):
        TaskCheckpointSubmission.model_validate({
            **payload,
            "status": "completed",
            "phase": "completed",
            "executionAttemptCount": 1,
            "executionDeadlineAt": "2026-09-11T08:01:00Z",
        })


@pytest.mark.parametrize(("status", "phase"), [
    ("created", "executing"),
    ("running", "planning"),
    ("waitingForConfirmation", "verifying"),
    ("completed", "failed"),
    ("failed", "completed"),
    ("cancelled", "executing"),
])
def test_rejects_invalid_task_status_phase_combinations(
    status: str,
    phase: str,
) -> None:
    with pytest.raises(ValidationError, match="does not allow phase"):
        TaskCheckpointSubmission.model_validate({
            "requestId": "checkpoint_invalid_state",
            "protocolVersion": "1.0",
            "taskId": "7c215f3c-e513-49cc-b645-20dfbb1aa954",
            "revision": 1,
            "toolCallId": "call_123",
            "capability": "reminder.create",
            "status": status,
            "phase": phase,
            "occurredAt": "2026-09-11T08:00:00Z",
        })
