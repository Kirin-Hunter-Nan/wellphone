"""Deterministic validation and enrichment for business-trip schedules."""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from app.tools.business_trip.models import (
    BusinessTripInput,
    BusinessTripPlanItem,
    BusinessTripPlanOutput,
    TripCommitment,
)
from app.tools.travel.validation import matching_place


def validate_business_trip_plan(
    plan: BusinessTripPlanOutput,
    requested: BusinessTripInput,
    state: dict[str, object],
) -> list[str]:
    issues: list[str] = []
    expected_dates = _date_range(requested.start_date, requested.end_date)
    if [day.date for day in plan.days] != expected_dates:
        issues.append(
            "days must cover every requested date exactly once and in chronological order"
        )
    if plan.time_zone != requested.time_zone:
        issues.append(f"timeZone must remain {requested.time_zone}")

    commitments = {item.id: item for item in requested.commitments}
    seen_commitments: list[str] = []
    places = state.get("places") if isinstance(state.get("places"), dict) else {}
    all_items: list[BusinessTripPlanItem] = []
    zone = ZoneInfo(requested.time_zone)

    for day in plan.days:
        previous_start = None
        for item in day.items:
            all_items.append(item)
            local_date = item.start_at.astimezone(zone).date()
            if local_date != day.date:
                issues.append(
                    f"{item.name} starts on {local_date.isoformat()} but is listed under "
                    f"{day.date.isoformat()}"
                )
            if previous_start is not None and item.start_at < previous_start:
                issues.append(f"{day.date.isoformat()} items must be chronological")
            previous_start = item.start_at

            if item.kind == "fixed":
                source = commitments.get(item.source_commitment_id or "")
                if source is None:
                    issues.append(
                        f"fixed item {item.name} references an unknown commitment"
                    )
                else:
                    seen_commitments.append(source.id)
                    issues.extend(_validate_fixed_item(item, source))

            if item.place_name:
                place = matching_place(item.place_name, places)
                if item.kind == "fixed":
                    # A booking or meeting remains authoritative even when MapKit cannot
                    # disambiguate its location. Enrichment drops the unverified map place
                    # while retaining the source location verbatim.
                    continue
                if place is None:
                    issues.append(
                        f"call places_search for {item.place_name} before submitting"
                    )
                elif place.get("verified") is not True:
                    issues.append(
                        f"places_search must verify {item.place_name} before submitting"
                    )

    for commitment_id in commitments:
        count = seen_commitments.count(commitment_id)
        if count != 1:
            issues.append(
                f"commitment {commitment_id} must appear exactly once; found {count}"
            )

    issues.extend(_validate_flexible_overlaps(all_items, commitments))
    return list(dict.fromkeys(issues))


def enrich_business_trip_plan(
    plan: BusinessTripPlanOutput,
    requested: BusinessTripInput,
    state: dict[str, object],
) -> dict[str, object]:
    output = plan.model_dump(mode="json", by_alias=True)
    places = state.get("places") if isinstance(state.get("places"), dict) else {}
    for day in output["days"]:
        for item in day["items"]:
            place_name = item.get("placeName")
            place = matching_place(str(place_name), places) if place_name else None
            if item.get("kind") == "fixed" and (
                place is None or place.get("verified") is not True
            ):
                item["placeName"] = None
                item["place"] = None
            else:
                item["place"] = place
    output["conflicts"] = detect_commitment_conflicts(requested)
    output["commitmentCount"] = len(requested.commitments)
    output["sourceCommitments"] = [
        item.model_dump(mode="json", by_alias=True)
        for item in requested.commitments
    ]
    return output


def detect_commitment_conflicts(
    requested: BusinessTripInput,
) -> list[dict[str, str]]:
    conflicts: list[dict[str, str]] = []
    ordered = sorted(
        (item for item in requested.commitments if item.kind != "hotel"),
        key=lambda item: item.start_at,
    )
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            if second.start_at >= first.end_at:
                break
            if second.end_at > first.start_at:
                conflicts.append({
                    "firstCommitmentId": first.id,
                    "secondCommitmentId": second.id,
                    "message": f"{first.title} 与 {second.title} 时间重叠",
                })
    return conflicts


def _validate_fixed_item(
    item: BusinessTripPlanItem, source: TripCommitment
) -> list[str]:
    issues: list[str] = []
    if item.name != source.title:
        issues.append(f"fixed commitment {source.id} must keep title {source.title}")
    if item.start_at != source.start_at or item.end_at != source.end_at:
        issues.append(f"fixed commitment {source.id} must keep its original time")
    if item.location != source.location:
        issues.append(f"fixed commitment {source.id} must keep location {source.location}")
    return issues


def _validate_flexible_overlaps(
    items: list[BusinessTripPlanItem],
    commitments: dict[str, TripCommitment],
) -> list[str]:
    issues: list[str] = []
    ordered = sorted(
        (
            item
            for item in items
            if not (
                item.kind == "fixed"
                and commitments.get(item.source_commitment_id or "") is not None
                and commitments[item.source_commitment_id or ""].kind == "hotel"
            )
        ),
        key=lambda item: item.start_at,
    )
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            if second.start_at >= first.end_at:
                break
            if first.kind != "fixed" or second.kind != "fixed":
                issues.append(f"{first.name} overlaps {second.name}")
    return issues


def _date_range(start: date, end: date) -> list[date]:
    values: list[date] = []
    current = start
    while current <= end:
        values.append(current)
        current += timedelta(days=1)
    return values
