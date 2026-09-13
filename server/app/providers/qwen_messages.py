"""Qwen message and request-payload construction."""

from datetime import datetime, timezone
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.api.protocol import ChatRequest, ToolResultSubmission
from app.providers.base import ProviderToolCallContext
from app.intents.resolver import IntentResolver
from app.tools.catalog import ModelToolCatalog


def initial_messages(
    request: ChatRequest,
    *,
    request_time: datetime | None = None,
) -> list[dict[str, object]]:
    current_time = request_time or datetime.now(timezone.utc)
    request_time_text = current_time.isoformat()
    time_zone = request.device_context.time_zone
    try:
        local_time = current_time.astimezone(ZoneInfo(time_zone))
        local_context = (
            f"The user's local date and time is {local_time.isoformat()}, "
            f"and the local weekday is {local_time.strftime('%A')}. "
        )
    except ZoneInfoNotFoundError:
        local_context = ""
    return [
        {
            "role": "system",
            "content": (
                "You are WellPhone, an iPhone assistant. "
                f"The current time is {request_time_text}. "
                f"The user's IANA time zone is {time_zone}. "
                f"{local_context}"
                "For each user turn, choose exactly one outcome. Reply normally for "
                "conversation or information requests. When the user intends to run a "
                "supported action but required information is missing or ambiguous, call "
                "intent_clarify instead of asking in ordinary assistant text. Call an action "
                "tool only when every required argument is known. "
                "Use reminder_create only when the user explicitly asks to create "
                "a reminder. Resolve relative dates to an absolute ISO 8601 "
                "date-time with an explicit UTC offset. For Chinese expressions such as "
                "下周六, use the named weekday in the next Monday-to-Sunday calendar week "
                "and verify that dueAt falls on that weekday. Never claim a reminder was "
                "created before the iPhone app reports the result. An explicit reminder "
                "request authorizes immediate creation; do not ask for confirmation again. "
                "Use travel_plan when the user asks you to create a travel itinerary and "
                "both exact start and end dates are known. It starts immediately as a background "
                "task, so never claim the itinerary is complete before its Tool result. Set "
                "addToCalendar=true only if the user explicitly asks to add the resulting "
                "itinerary to their calendar; otherwise leave it false. "
                "The trip origin is optional. Never ask for an origin merely to create a "
                "destination-local itinerary; when it is missing, plan transportation only "
                "within the destination. Ask for an origin only if the user explicitly requests "
                "intercity or round-trip transportation that cannot be planned without it. "
                "Use business_trip_plan when the user asks to organize a work trip around "
                "provided flight, hotel, meeting, or transport records. Extract fixed records "
                "from both text and attached images, preserve observed titles and times, assign "
                "stable short commitment ids, and include the user's destination IANA time zone. "
                "When the user naturally asks you to find trip evidence in Gmail, set "
                "searchGmail=true. The server constructs the narrow Gmail query from the "
                "destination and trip dates. Never construct gmailQuery yourself. Gmail search "
                "operators are an internal Tool detail: never require the user to write after:, "
                "before:, label:, from:, to:, or other Gmail syntax. "
                "Use intent_clarify if a required value cannot be determined reliably. Never "
                "invent a booking, confirmation code, or meeting. Set addToCalendar and "
                "addCalendarAlerts only when those writes were explicitly requested. "
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
    intents: IntentResolver,
) -> dict[str, object]:
    return {
        "model": model,
        "messages": messages,
        "tools": intents.model_definitions(),
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
