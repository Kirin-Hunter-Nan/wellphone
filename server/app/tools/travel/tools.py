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
        "place. Call it for every place before submitting an itinerary."
    )
    arguments_model = PlacesSearchArguments

    def __init__(self, maps: AppleMapsSearchClient) -> None:
        self._maps = maps

    async def execute(
        self, arguments: BaseModel, context: LoopToolContext
    ) -> LoopToolResult:
        values = PlacesSearchArguments.model_validate(arguments)
        requested = TravelPlanInput.model_validate(context.task.input)
        requested_destination = requested.destination.casefold()
        supplied_destination = values.destination.casefold()
        if (
            requested_destination not in supplied_destination
            and supplied_destination not in requested_destination
        ):
            return LoopToolResult({
                "ok": False,
                "error": {
                    "code": "destination_mismatch",
                    "message": f"Search destination must remain {requested.destination}",
                },
            })
        place = await self._maps.search(
            values.query, requested.destination, values.language
        )
        dumped = place.model_dump(mode="json")
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
