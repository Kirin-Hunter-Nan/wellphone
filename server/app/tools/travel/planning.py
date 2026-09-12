"""Compatibility facade for the travel-planning capability."""

from app.tools.travel.artifacts import build_travel_outcome
from app.tools.travel.maps import AppleMapsSearchClient
from app.tools.travel.models import (
    AppleMapsPlace,
    ItinerarySubmitArguments,
    PlacesSearchArguments,
    TravelPlanDay,
    TravelPlanInput,
    TravelPlanItem,
    TravelPlanOutput,
)
from app.tools.travel.profile import make_travel_profile
from app.tools.travel.tools import ItinerarySubmitTool, PlacesSearchTool

__all__ = [
    "AppleMapsPlace",
    "AppleMapsSearchClient",
    "ItinerarySubmitArguments",
    "ItinerarySubmitTool",
    "PlacesSearchArguments",
    "PlacesSearchTool",
    "TravelPlanDay",
    "TravelPlanInput",
    "TravelPlanItem",
    "TravelPlanOutput",
    "build_travel_outcome",
    "make_travel_profile",
]
