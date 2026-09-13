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
            matched_place = matching_place(item.name, places)
            if matched_place is None:
                issues.append(f"call places_search for {item.name} before submitting")
            elif matched_place.get("verified") is not True:
                issues.append(
                    f"places_search must verify {item.name} before submitting"
                )
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
    normalized = _normalize_place_alias(name)
    if not normalized:
        return None

    best_match: tuple[tuple[int, int], dict[str, object]] | None = None
    for query, place in places.items():
        if not isinstance(place, dict):
            continue

        # MapKit can return a canonical branch name that differs from the search
        # query only by punctuation or a suffix, for example:
        #   query: 龙美术馆 西岸馆
        #   name:  龙美术馆(西岸馆)
        # Prefer that authoritative name, while retaining the query as a fallback
        # for persisted results created before canonical-name matching existed.
        aliases = (
            (place.get("name"), 2),
            (query, 1),
        )
        for alias_value, source_priority in aliases:
            if not isinstance(alias_value, str):
                continue
            alias = _normalize_place_alias(alias_value)
            if not alias:
                continue
            if alias == normalized:
                score = (2, source_priority)
            elif min(len(alias), len(normalized)) >= 4 and (
                alias in normalized or normalized in alias
            ):
                score = (1, min(len(alias), len(normalized)))
            else:
                continue
            if best_match is None or score > best_match[0]:
                best_match = (score, place)

    return best_match[1] if best_match is not None else None


def _normalize_place_alias(value: str) -> str:
    """Normalize harmless formatting differences without fuzzy place matching."""
    return "".join(character for character in value.casefold() if character.isalnum())
