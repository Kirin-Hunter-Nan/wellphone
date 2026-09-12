"""Qwen message and request-payload construction."""

from datetime import datetime, timezone
import json

from app.api.protocol import ChatRequest, ToolResultSubmission
from app.providers.base import ProviderToolCallContext
from app.tools.catalog import ModelToolCatalog


def initial_messages(request: ChatRequest) -> list[dict[str, object]]:
    request_time = datetime.now(timezone.utc).isoformat()
    time_zone = request.device_context.time_zone
    return [
        {
            "role": "system",
            "content": (
                "You are WellPhone, an iPhone assistant. "
                f"The current time is {request_time}. "
                f"The user's IANA time zone is {time_zone}. "
                "Use reminder_create only when the user explicitly asks to create "
                "a reminder. Resolve relative dates to an absolute ISO 8601 "
                "date-time with an explicit UTC offset. Never claim a reminder was "
                "created: the iPhone app will ask for confirmation and report the result. "
                "Use travel_plan when the user asks you to create a travel itinerary and "
                "both exact start and end dates are known. It runs as a confirmed background "
                "task, so never claim the itinerary is complete before its Tool result. "
                "After receiving a Tool result, describe the outcome using only facts "
                "explicitly present in result or error. If result.payload includes "
                "dueAt and timeZone, reproduce them as supplied; never infer, recalculate, or "
                "contradict their time zone."
            ),
        },
        *request.provider_messages(),
    ]


def streaming_payload(
    model: str,
    messages: list[dict[str, object]],
    catalog: ModelToolCatalog,
) -> dict[str, object]:
    return {
        "model": model,
        "messages": messages,
        "tools": catalog.model_definitions(),
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "tool_stream": False,
        "stream": True,
        "stream_options": {"include_usage": True},
    }


def continuation_payload(
    model: str,
    messages: list[object],
    context: ProviderToolCallContext,
    result: ToolResultSubmission,
    catalog: ModelToolCatalog,
) -> dict[str, object]:
    return {
        "model": model,
        "messages": [
            *messages,
            {
                "role": "tool",
                "tool_call_id": context.tool_call_id,
                "content": json.dumps(
                    result.semantic_payload(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ],
        "tools": catalog.model_definitions(),
        "tool_choice": "none",
        "stream": False,
    }
