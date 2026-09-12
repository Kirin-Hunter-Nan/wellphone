"""Itinerary validation and enrichment against searched places."""

from datetime import date, timedelta

from app.tools.travel.models import TravelPlanInput, TravelPlanOutput


def validate_plan(
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
            if matching_place(item.name, places) is None:
                issues.append(f"call places_search for {item.name} before submitting")
    return issues


def enrich_plan(
    plan: TravelPlanOutput, state: dict[str, object]
) -> dict[str, object]:
    places = state.get("places") if isinstance(state.get("places"), dict) else {}
    for day in plan.days:
        for item in day.items:
            item.place = matching_place(item.name, places)
    return plan.model_dump(mode="json", by_alias=True)


def matching_place(name: str, places: dict[str, object]) -> dict[str, object] | None:
    normalized = name.casefold()
    for query, place in places.items():
        if (normalized in query or query in normalized) and isinstance(place, dict):
            return place
    return None
