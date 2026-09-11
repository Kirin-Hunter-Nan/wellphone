from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PROTOCOL_VERSION = "1.0"


class TextContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]
    text: str

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Text content must not be empty")
        return value


class ImageURLValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value.startswith(("https://", "data:image/")):
            raise ValueError("Images must use HTTPS or a data:image URI")
        return value


class ImageContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["image_url"]
    image_url: ImageURLValue


ContentPart = Annotated[
    TextContentPart | ImageContentPart,
    Field(discriminator="type"),
]


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str | list[ContentPart]

    @model_validator(mode="after")
    def validate_content(self) -> "ChatMessage":
        if isinstance(self.content, str) and not self.content.strip():
            raise ValueError("Message content must not be empty")
        if isinstance(self.content, list) and not self.content:
            raise ValueError("Message content parts must not be empty")
        return self


class DeviceContext(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    locale: str
    time_zone: str = Field(alias="timeZone")
    capability_set_version: str = Field(alias="capabilitySetVersion")


class ChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request_id: str = Field(alias="requestId", min_length=1)
    protocol_version: Literal["1.0"] = Field(alias="protocolVersion")
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    device_context: DeviceContext = Field(alias="deviceContext")

    @model_validator(mode="after")
    def validate_final_message(self) -> "ChatRequest":
        if self.messages[-1].role != "user":
            raise ValueError("The final message must have the user role")
        return self

    def provider_messages(self) -> list[dict[str, object]]:
        return [message.model_dump(mode="json") for message in self.messages]


class ToolRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    tool_call_id: str = Field(alias="toolCallId", min_length=1)
    capability: str = Field(min_length=1)
    arguments: dict[str, object]


def encode_sse(event: dict[str, object]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"


def response_started(response_id: str) -> dict[str, object]:
    return {
        "type": "response.started",
        "protocolVersion": PROTOCOL_VERSION,
        "responseId": response_id,
    }


def assistant_delta(response_id: str, text: str) -> dict[str, object]:
    return {
        "type": "assistant.delta",
        "protocolVersion": PROTOCOL_VERSION,
        "responseId": response_id,
        "text": text,
    }


def tool_requested(response_id: str, request: ToolRequest) -> dict[str, object]:
    return {
        "type": "tool.requested",
        "protocolVersion": PROTOCOL_VERSION,
        "responseId": response_id,
        **request.model_dump(mode="json", by_alias=True),
    }


def response_completed(response_id: str) -> dict[str, object]:
    return {
        "type": "response.completed",
        "protocolVersion": PROTOCOL_VERSION,
        "responseId": response_id,
    }


def response_failed(response_id: str, code: str, message: str) -> dict[str, object]:
    return {
        "type": "response.failed",
        "protocolVersion": PROTOCOL_VERSION,
        "responseId": response_id,
        "error": {"code": code, "message": message},
    }
