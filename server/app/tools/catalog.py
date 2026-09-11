from __future__ import annotations

import json
from typing import Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from app.protocol import ToolRequest


class ToolCatalogError(ValueError):
    pass


class ReminderCreateArguments(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    title: str = Field(
        min_length=1,
        max_length=200,
        description="Short reminder title in the user's language.",
    )
    due_at: AwareDatetime = Field(
        alias="dueAt",
        description="Absolute ISO 8601 date-time with an explicit UTC offset.",
    )
    notes: str | None = Field(
        default=None,
        max_length=2_000,
        description="Optional reminder notes.",
    )
    list_name: str | None = Field(
        default=None,
        alias="listName",
        description="Optional Apple Reminders list name.",
    )

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Reminder title must not be empty")
        return stripped

    @field_validator("notes", "list_name")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ModelToolCatalog:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[str, type[BaseModel], str]] = {
            "reminder_create": (
                "reminder.create",
                ReminderCreateArguments,
                "Prepare one Apple Reminders item for explicit user confirmation. "
                "Do not use for general questions or discussions about reminders.",
            ),
        }

    def model_definitions(self) -> list[dict[str, object]]:
        definitions: list[dict[str, object]] = []
        for model_name, (_, arguments_type, description) in self._tools.items():
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": model_name,
                        "description": description,
                        "parameters": arguments_type.model_json_schema(by_alias=True),
                    },
                }
            )
        return definitions

    def normalize(
        self,
        *,
        model_name: str,
        tool_call_id: str,
        arguments_json: str,
    ) -> ToolRequest:
        registered = self._tools.get(model_name)
        if registered is None:
            raise ToolCatalogError(f"Unregistered model tool: {model_name}")

        capability, arguments_type, _ = registered
        try:
            raw_arguments = json.loads(arguments_json)
            arguments = arguments_type.model_validate(raw_arguments)
        except (json.JSONDecodeError, ValidationError) as error:
            raise ToolCatalogError(f"Invalid arguments for {model_name}") from error

        dumped: dict[str, Any] = arguments.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        return ToolRequest(
            toolCallId=tool_call_id,
            capability=capability,
            arguments=dumped,
        )
