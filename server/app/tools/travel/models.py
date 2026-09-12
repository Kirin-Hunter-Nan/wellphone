"""Travel task input and itinerary schemas."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class PlacesSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=200)
    language: str = Field(default="zh-CN", min_length=2, max_length=20)


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
