"""Travel planning tools, validation, and artifact rendering."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agent.profile import AgentTaskProfile
from app.agent.tools import LoopToolContext, LoopToolResult
from app.tasks.models import ArtifactDraft, ServerTask, TaskOutcome


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


class AppleMapsSearchClient:
    def __init__(self, token: str | None, client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=20)
        self._owns_client = client is None

    async def search(
        self, query: str, destination: str, language: str = "zh-CN"
    ) -> AppleMapsPlace:
        fallback_url = "https://maps.apple.com/?" + urlencode(
            {"q": f"{query} {destination}"}
        )
        if not self._token:
            return AppleMapsPlace(name=query, map_url=fallback_url, verified=False)
        response = await self._client.get(
            "https://maps-api.apple.com/v1/search",
            headers={"Authorization": f"Bearer {self._token}"},
            params={"q": f"{query} {destination}", "lang": language},
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return AppleMapsPlace(name=query, map_url=fallback_url, verified=False)
        result = results[0]
        coordinate = result.get("coordinate") or {}
        latitude, longitude = coordinate.get("latitude"), coordinate.get("longitude")
        map_url = fallback_url
        if latitude is not None and longitude is not None:
            map_url = "https://maps.apple.com/?" + urlencode({
                "q": result.get("name") or query,
                "ll": f"{latitude},{longitude}",
            })
        return AppleMapsPlace(
            name=result.get("name") or query,
            formatted_address=result.get("formattedAddress"),
            latitude=latitude,
            longitude=longitude,
            map_url=map_url,
            verified=True,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class PlacesSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=200)
    language: str = Field(default="zh-CN", min_length=2, max_length=20)


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
        issues = _validate_plan(values.plan, requested, context.state)
        if issues:
            return LoopToolResult({
                "ok": False,
                "status": "needs_revision",
                "issues": issues,
            })
        enriched = _enrich_plan(values.plan, context.state)
        return LoopToolResult(
            observation={"ok": True, "status": "accepted"},
            final_output=enriched,
        )


def make_travel_profile() -> AgentTaskProfile:
    return AgentTaskProfile(
        capability="travel.plan",
        allowed_tools=("places_search", "itinerary_submit"),
        steps=("理解旅行目标", "自主检索与规划", "校验并修订", "生成最终行程"),
        max_iterations=12,
        max_tool_calls=112,
        system_prompt=(
            "你是 WellPhone 的旅行规划 Agent，运行在一个有预算上限的工具循环中。"
            "理解用户日期、目的地、同行者、节奏和偏好后，自主决定搜索哪些地点。"
            "每一个进入行程的具体地点都必须先调用 places_search 核对。"
            "互不依赖的地点应在同一轮并行发起多个 places_search，避免无意义的逐个等待。"
            "不要虚构营业时间、票价或地图核对结果。按地理邻近性组织每天的安排，"
            "为移动和休息保留合理时间。完成后必须调用 itinerary_submit；如果它返回"
            "校验问题，依据 Observation 局部修订并再次提交。不要直接用普通文本结束任务。"
        ),
        finalize=build_travel_outcome,
        parse_final_content=lambda _: None,
    )


def build_travel_outcome(
    task: ServerTask, output: dict[str, object]
) -> TaskOutcome:
    travel = TravelPlanInput.model_validate(task.input)
    plan = TravelPlanOutput.model_validate(output).model_dump(mode="json", by_alias=True)
    markdown = _render_markdown(plan, travel)
    calendar = _calendar_payload(plan, travel)
    title = str(plan["title"])
    return TaskOutcome(
        summary=f"{title}已完成，共规划 {len(plan['days'])} 天。",
        artifacts=(
            ArtifactDraft(
                kind="itinerary", title=title,
                content_type="application/vnd.wellphone.itinerary+json", payload=plan,
            ),
            ArtifactDraft(
                kind="text", title=f"{title}（文本版）",
                content_type="text/markdown", payload=markdown,
            ),
            ArtifactDraft(
                kind="json", title=f"{title}（日历事件）",
                content_type="application/vnd.wellphone.calendar-events+json", payload=calendar,
            ),
        ),
    )


def _validate_plan(
    plan: TravelPlanOutput,
    requested: TravelPlanInput,
    state: dict[str, object],
) -> list[str]:
    issues: list[str] = []
    expected_dates: list[date] = []
    current = requested.start_date
    while current <= requested.end_date:
        expected_dates.append(current)
        current += timedelta(days=1)
    if [day.date for day in plan.days] != expected_dates:
        issues.append(
            "days must cover every requested date exactly once and in chronological order"
        )
    daily_limit = {"relaxed": 3, "balanced": 5, "intensive": 7}[requested.pace]
    places = state.get("places") if isinstance(state.get("places"), dict) else {}
    for day in plan.days:
        if len(day.items) > daily_limit:
            issues.append(
                f"{day.date.isoformat()} has {len(day.items)} places; "
                f"{requested.pace} pace allows {daily_limit}"
            )
        previous_end = -1
        for item in day.items:
            hour, minute = (int(value) for value in item.time.split(":"))
            start = hour * 60 + minute
            if start < previous_end:
                issues.append(
                    f"{day.date.isoformat()} has overlapping items near {item.name}"
                )
            previous_end = start + item.duration_minutes
            if _matching_place(item.name, places) is None:
                issues.append(f"call places_search for {item.name} before submitting")
    return issues


def _enrich_plan(
    plan: TravelPlanOutput, state: dict[str, object]
) -> dict[str, object]:
    places = state.get("places") if isinstance(state.get("places"), dict) else {}
    for day in plan.days:
        for item in day.items:
            item.place = _matching_place(item.name, places)
    return plan.model_dump(mode="json", by_alias=True)


def _matching_place(
    name: str, places: dict[str, object]
) -> dict[str, object] | None:
    normalized = name.casefold()
    for query, place in places.items():
        if (normalized in query or query in normalized) and isinstance(place, dict):
            return place
    return None


def _render_markdown(plan: dict[str, object], travel: TravelPlanInput) -> str:
    lines = [
        f"# {plan.get('title') or travel.destination + '旅行计划'}",
        "",
        str(plan.get("overview") or ""),
        "",
    ]
    for day in plan.get("days", []):
        lines.extend([f"## {day.get('date', '')} · {day.get('theme', '')}", ""])
        for item in day.get("items", []):
            place = item.get("place") or {}
            lines.append(
                f"- **{item.get('time', '')} {item.get('name', '')}**"
                f"（约 {item.get('durationMinutes', '')} 分钟）"
            )
            if item.get("notes"):
                lines.append(f"  {item['notes']}")
            if place.get("map_url"):
                lines.append(f"  [在 Apple 地图中打开]({place['map_url']})")
        lines.append("")
    return "\n".join(lines).strip()


def _calendar_payload(
    plan: dict[str, object], travel: TravelPlanInput
) -> dict[str, object]:
    events: list[dict[str, object]] = []
    for day in plan.get("days", []):
        day_value = str(day.get("date") or "")
        for item in day.get("items", []):
            time_value = str(item.get("time") or "09:00")
            try:
                start = datetime.fromisoformat(f"{day_value}T{time_value}:00")
            except ValueError:
                continue
            duration = int(item.get("durationMinutes") or 60)
            place = item.get("place") or {}
            events.append({
                "title": item.get("name"),
                "startLocal": start.isoformat(),
                "endLocal": (start + timedelta(minutes=duration)).isoformat(),
                "location": place.get("formatted_address") or item.get("name"),
                "url": place.get("map_url"),
                "notes": item.get("notes"),
            })
    return {
        "timeZoneMode": "destination",
        "timeZone": plan.get("timeZone"),
        "destination": travel.destination,
        "events": events,
    }
