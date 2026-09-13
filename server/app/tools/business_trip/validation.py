"""Deterministic validation and enrichment for business-trip schedules."""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from app.tools.business_trip.models import (
    BusinessTripInput,
    BusinessTripPlanItem,
    BusinessTripPlanOutput,
    CalendarEvent,
    TripCommitment,
)
from app.tools.travel.validation import matching_place
from app.tools.business_trip.reimbursement import build_reimbursement_report
from app.tools.travel.models import AppleMapsRoute


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
    routes = state.get("routes") if isinstance(state.get("routes"), dict) else {}
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
            if item.kind == "transfer":
                issues.extend(_validate_transfer_route(item, routes))

    for commitment_id in commitments:
        count = seen_commitments.count(commitment_id)
        if count != 1:
            issues.append(
                f"commitment {commitment_id} must appear exactly once; found {count}"
            )

    issues.extend(_validate_flexible_overlaps(all_items, commitments))
    issues.extend(
        _validate_flexible_calendar_overlaps(
            all_items, _calendar_events_from_state(state)
        )
    )
    return list(dict.fromkeys(issues))


def enrich_business_trip_plan(
    plan: BusinessTripPlanOutput,
    requested: BusinessTripInput,
    state: dict[str, object],
) -> dict[str, object]:
    output = plan.model_dump(mode="json", by_alias=True)
    places = state.get("places") if isinstance(state.get("places"), dict) else {}
    routes = state.get("routes") if isinstance(state.get("routes"), dict) else {}
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
            route_id = item.get("routeId")
            item["route"] = routes.get(route_id) if isinstance(route_id, str) else None
    calendar_events = _calendar_events_from_state(state)
    output["conflicts"] = detect_commitment_conflicts(requested, calendar_events)
    output["calendarEventCount"] = len(calendar_events)
    output["commitmentCount"] = len(requested.commitments)
    output["sourceCommitments"] = [
        item.model_dump(mode="json", by_alias=True)
        for item in requested.commitments
    ]
    output["reimbursement"] = build_reimbursement_report(requested)
    output["sourceReceipts"] = [
        item.model_dump(mode="json", by_alias=True) for item in requested.receipts
    ]
    return output


def detect_commitment_conflicts(
    requested: BusinessTripInput,
    calendar_events: list[CalendarEvent] | None = None,
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
    for commitment in ordered:
        for event in calendar_events or []:
            # All-day entries such as subscribed public holidays provide useful
            # context, but they do not prove that the user is unavailable for
            # every timed commitment on that date.
            if event.is_all_day:
                continue
            if event.start_at >= commitment.end_at or event.end_at <= commitment.start_at:
                continue
            if _same_calendar_event(commitment, event):
                continue
            conflicts.append({
                "firstCommitmentId": commitment.id,
                "secondCalendarEventId": event.id,
                "message": f"{commitment.title} 与日历事件「{event.title}」时间重叠",
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


def _validate_transfer_route(
    item: BusinessTripPlanItem,
    routes: dict[str, object],
) -> list[str]:
    if not item.route_id or not item.origin_place_name or not item.destination_place_name:
        return [
            f"transfer {item.name} requires routeId, originPlaceName, and "
            "destinationPlaceName from routes_search"
        ]
    raw_route = routes.get(item.route_id)
    if not isinstance(raw_route, dict):
        return [f"call routes_search before submitting transfer {item.name}"]
    try:
        route = AppleMapsRoute.model_validate(raw_route)
    except Exception:
        return [f"routeId {item.route_id} is invalid"]
    issues: list[str] = []
    if item.origin_place_name != route.origin_name:
        issues.append(f"transfer {item.name} must keep route origin {route.origin_name}")
    if item.destination_place_name != route.destination_name:
        issues.append(
            f"transfer {item.name} must keep route destination {route.destination_name}"
        )
    if item.start_at != route.departure_at:
        issues.append(f"transfer {item.name} must start at the queried route departureAt")
    planned_minutes = (item.end_at - item.start_at).total_seconds() / 60
    if planned_minutes < route.expected_travel_time_minutes:
        issues.append(
            f"transfer {item.name} allows {planned_minutes:.0f} minutes but MapKit requires "
            f"{route.expected_travel_time_minutes} minutes"
        )
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


def _validate_flexible_calendar_overlaps(
    items: list[BusinessTripPlanItem],
    calendar_events: list[CalendarEvent],
) -> list[str]:
    issues: list[str] = []
    for item in items:
        if item.kind == "fixed":
            continue
        for event in calendar_events:
            # Keep all-day events in the tool observation for context, while
            # treating only timed busy events as hard scheduling constraints.
            if event.is_all_day:
                continue
            if event.start_at < item.end_at and event.end_at > item.start_at:
                issues.append(
                    f"{item.name} overlaps existing calendar event {event.title}"
                )
    return issues


def _same_calendar_event(
    commitment: TripCommitment,
    event: CalendarEvent,
) -> bool:
    return (
        " ".join(commitment.title.casefold().split())
        == " ".join(event.title.casefold().split())
        and commitment.start_at == event.start_at
        and commitment.end_at == event.end_at
    )


def _calendar_events_from_state(state: dict[str, object]) -> list[CalendarEvent]:
    values = state.get("calendarEvents")
    if not isinstance(values, list):
        return []
    return [CalendarEvent.model_validate(item) for item in values]


def _date_range(start: date, end: date) -> list[date]:
    values: list[date] = []
    current = start
    while current <= end:
        values.append(current)
        current += timedelta(days=1)
    return values
