from __future__ import annotations

import json
from datetime import date
from typing import Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.api.protocol import ToolRequest


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


class TravelPlanArguments(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    destination: str = Field(min_length=1, max_length=200)
    start_date: date = Field(alias="startDate")
    end_date: date = Field(alias="endDate")
    origin: str | None = Field(default=None, max_length=200)
    preferences: list[str] = Field(default_factory=list, max_length=12)
    pace: str = Field(default="balanced", pattern="^(relaxed|balanced|intensive)$")
    travelers: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_trip_length(self) -> "TravelPlanArguments":
        duration = (self.end_date - self.start_date).days + 1
        if duration < 1 or duration > 14:
            raise ValueError("Trip duration must be between 1 and 14 days")
        return self


class ModelToolCatalog:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[str, type[BaseModel], str, str]] = {
            "reminder_create": (
                "reminder.create",
                ReminderCreateArguments,
                "Prepare one Apple Reminders item for explicit user confirmation. "
                "Do not use for general questions or discussions about reminders.",
                "device",
            ),
            "travel_plan": (
                "travel.plan",
                TravelPlanArguments,
                "Start a multi-step travel planning task after the user has supplied a "
                "destination and exact start and end dates. Ask for missing dates before "
                "using this tool. Origin is optional: do not ask for it unless the user "
                "explicitly requests intercity or round-trip transportation. Without an "
                "origin, plan only transportation within the destination. The app will "
                "request confirmation before starting.",
                "server",
            ),
        }

    def model_definitions(self) -> list[dict[str, object]]:
        definitions: list[dict[str, object]] = []
        for model_name, (_, arguments_type, description, _) in self._tools.items():
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

        capability, arguments_type, _, execution_location = registered
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
            executionLocation=execution_location,
            arguments=dumped,
        )
