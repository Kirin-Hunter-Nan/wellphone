"""Atomic tools available to the travel-planning Agent."""

from pydantic import BaseModel

from app.agent.tools import LoopToolContext, LoopToolResult
from app.tools.travel.maps import AppleMapsSearchClient
from app.tools.travel.models import (
    ItinerarySubmitArguments,
    PlacesSearchArguments,
    TravelPlanInput,
)
from app.tools.travel.validation import enrich_plan, validate_plan


class PlacesSearchTool:
    name = "places_search"
    description = (
        "Search Apple Maps for one concrete attraction, restaurant, cafe, hotel, or other "
        "place. Call it for every place before submitting an itinerary, then copy the "
        "returned place.name exactly into the itinerary item name."
    )
    arguments_model = PlacesSearchArguments

    def __init__(self, maps: AppleMapsSearchClient) -> None:
        self._maps = maps

    async def execute(
        self, arguments: BaseModel, context: LoopToolContext
    ) -> LoopToolResult:
        values = PlacesSearchArguments.model_validate(arguments)
        requested_destination_value = context.task.input.get("destination")
        if not isinstance(requested_destination_value, str) or not requested_destination_value.strip():
            return LoopToolResult({
                "ok": False,
                "error": {
                    "code": "missing_task_destination",
                    "message": "Task input does not contain a destination",
                },
            })
        requested_destination_value = requested_destination_value.strip()
        requested_destination = requested_destination_value.casefold()
        supplied_destination = values.destination.casefold()
        if (
            requested_destination not in supplied_destination
            and supplied_destination not in requested_destination
        ):
            return LoopToolResult({
                "ok": False,
                "error": {
                    "code": "destination_mismatch",
                    "message": f"Search destination must remain {requested_destination_value}",
                },
            })
        place = await self._maps.search(
            values.query,
            requested_destination_value,
            values.language,
            task_id=context.task.id,
            tool_call_id=context.tool_call_id,
        )
        dumped = place.model_dump(mode="json")
        if not place.verified:
            return LoopToolResult({
                "ok": False,
                "error": {
                    "code": "place_not_verified",
                    "message": (
                        place.verification_error
                        or f"MapKit 无法唯一确认地点：{values.query}"
                    ),
                },
                "place": dumped,
            })
        return LoopToolResult(
            observation={"ok": True, "place": dumped},
            state_updates={"places": {values.query.casefold(): dumped}},
        )


class ItinerarySubmitTool:
    name = "itinerary_submit"
    description = (
        "Validate and submit the final itinerary. If validation returns issues, revise only "
        "the affected parts, search any missing places, and submit again."
    )
    arguments_model = ItinerarySubmitArguments

    async def execute(
        self, arguments: BaseModel, context: LoopToolContext
    ) -> LoopToolResult:
        values = ItinerarySubmitArguments.model_validate(arguments)
        requested = TravelPlanInput.model_validate(context.task.input)
        issues = validate_plan(values.plan, requested, context.state)
        if issues:
            return LoopToolResult({
                "ok": False,
                "status": "needs_revision",
                "issues": issues,
            })
        return LoopToolResult(
            observation={"ok": True, "status": "accepted"},
            final_output=enrich_plan(values.plan, context.state),
        )
