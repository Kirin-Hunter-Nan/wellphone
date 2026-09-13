"""Domain models for long-running server tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TaskStatus = Literal[
    "waitingForConfirmation", "queued", "running", "completed", "failed", "cancelled"
]


class ServerTaskCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    capability: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    input: dict[str, object]
    requires_confirmation: bool = Field(default=False, alias="requiresConfirmation")
    tool_call_id: str | None = Field(default=None, alias="toolCallId", min_length=1)


class ServerTaskStep(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    order: int
    title: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    detail: str | None = None


class ServerTaskArtifact(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    task_id: UUID = Field(alias="taskId")
    kind: str
    title: str
    content_type: str = Field(alias="contentType")
    payload: object | None = None
    storage_reference: str | None = Field(default=None, alias="storageReference")
    created_at: datetime = Field(alias="createdAt")


class ServerTask(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    conversation_id: UUID = Field(alias="conversationId")
    capability: str
    tool_call_id: str | None = Field(default=None, alias="toolCallId")
    title: str
    input: dict[str, object]
    status: TaskStatus
    phase: str
    progress: float = Field(ge=0, le=1)
    detail: str | None = None
    result_summary: str | None = Field(default=None, alias="resultSummary")
    error_message: str | None = Field(default=None, alias="errorMessage")
    requires_confirmation: bool = Field(alias="requiresConfirmation")
    attempt_count: int = Field(alias="attemptCount")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    steps: list[ServerTaskStep] = []
    artifacts: list[ServerTaskArtifact] = []


class DeviceToolCall(BaseModel):
    """Durable request/result boundary for work that must execute on the phone."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: UUID = Field(alias="taskId")
    tool_call_id: str = Field(alias="toolCallId", min_length=1)
    tool_name: str = Field(alias="toolName", min_length=1)
    arguments: dict[str, object]
    status: Literal["pending", "completed", "failed"]
    result: dict[str, object] | None = None
    error_code: str | None = Field(default=None, alias="errorCode")
    error_message: str | None = Field(default=None, alias="errorMessage")
    requested_at: datetime = Field(alias="requestedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")


class DeviceToolConflictError(ValueError):
    """Raised when a device Tool id is reused with different semantics."""


@dataclass(frozen=True, slots=True)
class ArtifactDraft:
    kind: str
    title: str
    content_type: str
    payload: object | None = None
    storage_reference: str | None = None


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    summary: str
    artifacts: tuple[ArtifactDraft, ...] = ()
