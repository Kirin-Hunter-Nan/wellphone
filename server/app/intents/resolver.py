"""Resolve a model turn into chat, clarification, or an executable action."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.intents.models import ActionIntent, ChatIntent, ClarifyIntent, IntentResolution
from app.tools.catalog import ModelToolCatalog, ToolCatalogError


CLARIFY_TOOL_NAME = "intent_clarify"


class IntentResolutionError(ValueError):
    pass


class ClarificationArguments(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    capability: str = Field(
        min_length=1,
        description="Platform capability the user appears to want.",
    )
    question: str = Field(
        min_length=1,
        max_length=500,
        description="One concise question in the user's language.",
    )
    missing_fields: list[str] = Field(
        alias="missingFields",
        min_length=1,
        max_length=8,
        description="Required or ambiguous fields that prevent safe execution.",
    )

    @field_validator("capability", "question")
    @classmethod
    def strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Text must not be empty")
        return stripped

    @field_validator("missing_fields")
    @classmethod
    def normalize_missing_fields(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized:
            raise ValueError("At least one missing field is required")
        return list(dict.fromkeys(normalized))


class IntentResolver:
    """Own the model-facing intent contract and normalize its decisions."""

    def __init__(self, catalog: ModelToolCatalog) -> None:
        self._catalog = catalog

    def model_definitions(self) -> list[dict[str, object]]:
        clarification_schema = ClarificationArguments.model_json_schema(by_alias=True)
        capability_schema = clarification_schema["properties"]["capability"]
        capability_schema["enum"] = list(self._catalog.capabilities())
        return [
            *self._catalog.model_definitions(),
            {
                "type": "function",
                "function": {
                    "name": CLARIFY_TOOL_NAME,
                    "description": (
                        "Ask one focused follow-up question when the user intends to run "
                        "a supported action but required information is missing or ambiguous. "
                        "Do not use for general conversation, advice, or unsupported actions."
                    ),
                    "parameters": clarification_schema,
                },
            },
        ]

    def resolve_chat(self, assistant_text: str) -> ChatIntent:
        if not assistant_text.strip():
            raise IntentResolutionError("A chat intent requires assistant text")
        return ChatIntent()

    def resolve_tool(
        self,
        *,
        model_name: str,
        tool_call_id: str,
        arguments_json: str,
    ) -> IntentResolution:
        if model_name != CLARIFY_TOOL_NAME:
            try:
                request = self._catalog.normalize(
                    model_name=model_name,
                    tool_call_id=tool_call_id,
                    arguments_json=arguments_json,
                )
            except ToolCatalogError as error:
                raise IntentResolutionError(str(error)) from error
            return ActionIntent(request=request)

        try:
            raw_arguments = json.loads(arguments_json)
            arguments = ClarificationArguments.model_validate(raw_arguments)
        except (json.JSONDecodeError, ValidationError) as error:
            raise IntentResolutionError("Invalid clarification intent") from error
        if arguments.capability not in self._catalog.capabilities():
            raise IntentResolutionError(
                f"Unsupported clarification capability: {arguments.capability}"
            )
        return ClarifyIntent(
            question=arguments.question,
            capability=arguments.capability,
            missing_fields=tuple(arguments.missing_fields),
        )
