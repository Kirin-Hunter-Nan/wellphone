import json

import pytest
from pydantic import ValidationError

from app.protocol import ChatRequest, assistant_delta, encode_sse


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
