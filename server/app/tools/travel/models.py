"""Travel task input, place, route, and itinerary schemas."""

from datetime import date
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class TravelPlanInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    destination: str = Field(min_length=1, max_length=200)
    start_date: date = Field(alias="startDate")
    end_date: date = Field(alias="endDate")
    origin: str | None = Field(default=None, max_length=200)
    preferences: list[str] = Field(default_factory=list, max_length=12)
    pace: Literal["relaxed", "balanced", "intensive"] = "balanced"
    travelers: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=2_000)
    add_to_calendar: bool = Field(default=False, alias="addToCalendar")

    @field_validator("destination")
    @classmethod
    def strip_destination(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_dates(self) -> "TravelPlanInput":
        duration = (self.end_date - self.start_date).days + 1
        if duration < 1:
            raise ValueError("endDate must not be before startDate")
        if duration > 14:
            raise ValueError("Travel plans are limited to 14 days")
        return self


class AppleMapsPlace(BaseModel):
    name: str
    formatted_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    map_url: str
    verified: bool
    place_id: str | None = None
    category: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    source: str | None = None
    verification_error: str | None = None
    candidate_count: int | None = Field(default=None, ge=0)


class PlacesSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=200)
    language: str = Field(default="zh-CN", min_length=2, max_length=20)


class RouteSearchArguments(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    origin_place_name: str = Field(alias="originPlaceName", min_length=1, max_length=300)
    destination_place_name: str = Field(
        alias="destinationPlaceName", min_length=1, max_length=300
    )
    departure_at: AwareDatetime = Field(alias="departureAt")
    transport_type: Literal["automobile", "transit", "walking"] = Field(
        default="automobile", alias="transportType"
    )


class AppleMapsRoute(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str = Field(min_length=1, max_length=500)
    origin_name: str = Field(alias="originName", min_length=1, max_length=300)
    destination_name: str = Field(
        alias="destinationName", min_length=1, max_length=300
    )
    transport_type: Literal["automobile", "transit", "walking"] = Field(
        alias="transportType"
    )
    distance_meters: float = Field(alias="distanceMeters", ge=0)
    expected_travel_time_minutes: int = Field(
        alias="expectedTravelTimeMinutes", ge=1, le=1_440
    )
    departure_at: AwareDatetime = Field(alias="departureAt")
    expected_arrival_at: AwareDatetime = Field(alias="expectedArrivalAt")
    map_url: str = Field(alias="mapURL", min_length=1, max_length=2_000)
    source: str = Field(default="mapkit-directions", max_length=100)

    @model_validator(mode="after")
    def validate_route_interval(self) -> "AppleMapsRoute":
        if self.expected_arrival_at <= self.departure_at:
            raise ValueError("expectedArrivalAt must be after departureAt")
        return self


class TravelPlanItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    name: str = Field(min_length=1, max_length=200)
    duration_minutes: int = Field(alias="durationMinutes", ge=15, le=720)
    notes: str | None = Field(default=None, max_length=2_000)
    place: dict[str, object] | None = None


class TravelPlanDay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    theme: str = Field(min_length=1, max_length=200)
    items: list[TravelPlanItem] = Field(min_length=1, max_length=8)


class TravelPlanOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    overview: str = Field(min_length=1, max_length=4_000)
    time_zone: str = Field(alias="timeZone", min_length=1, max_length=100)
    days: list[TravelPlanDay] = Field(min_length=1, max_length=14)


class ItinerarySubmitArguments(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    plan: TravelPlanOutput
