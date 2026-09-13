"""Artifact construction for the business-trip capability."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.tasks.models import ArtifactDraft, ServerTask, TaskOutcome
from app.tools.business_trip.models import BusinessTripInput


def build_business_trip_outcome(
    task: ServerTask, output: dict[str, object]
) -> TaskOutcome:
    requested = _resolved_request(task, output)
    title = str(output.get("title") or f"{requested.destination}商务出差计划")
    conflicts = output.get("conflicts") if isinstance(output.get("conflicts"), list) else []
    markdown = render_business_trip_markdown(output, requested)
    calendar = business_trip_calendar_payload(output, requested)
    report = {
        "destination": requested.destination,
        "commitmentCount": len(requested.commitments),
        "conflicts": conflicts,
        "checklist": output.get("checklist") or [],
        "driveFile": output.get("driveFile"),
    }
    drive_file = output.get("driveFile") if isinstance(output.get("driveFile"), dict) else {}
    drive_reference = (
        drive_file.get("webViewLink")
        if isinstance(drive_file.get("webViewLink"), str)
        else None
    )
    conflict_text = f"，发现 {len(conflicts)} 处时间冲突" if conflicts else "，未发现时间冲突"
    return TaskOutcome(
        summary=(
            f"{title}已完成，整理 {len(requested.commitments)} 项固定安排"
            f"{conflict_text}。"
        ),
        artifacts=(
            ArtifactDraft(
                kind="itinerary",
                title=title,
                content_type="application/vnd.wellphone.business-trip+json",
                payload=output,
            ),
            ArtifactDraft(
                kind="text",
                title=f"{title}（文本版）",
                content_type="text/markdown",
                payload=markdown,
                storage_reference=drive_reference,
            ),
            ArtifactDraft(
                kind="json",
                title=f"{title}（冲突与待办）",
                content_type="application/vnd.wellphone.business-trip-report+json",
                payload=report,
            ),
            ArtifactDraft(
                kind="json",
                title=f"{title}（日历事件）",
                content_type="application/vnd.wellphone.calendar-events+json",
                payload=calendar,
            ),
        ),
    )


def _resolved_request(
    task: ServerTask, output: dict[str, object]
) -> BusinessTripInput:
    commitments = output.get("sourceCommitments")
    if not isinstance(commitments, list):
        return BusinessTripInput.model_validate(task.input)
    return BusinessTripInput.model_validate({
        **task.input,
        "commitments": commitments,
    })


def render_business_trip_markdown(
    output: dict[str, object], requested: BusinessTripInput
) -> str:
    lines = [
        f"# {output.get('title') or requested.destination + '商务出差计划'}",
        "",
        str(output.get("overview") or ""),
        "",
    ]
    conflicts = output.get("conflicts") or []
    if conflicts:
        lines.extend(["## 时间冲突", ""])
        for conflict in conflicts:
            if isinstance(conflict, dict):
                lines.append(f"- {conflict.get('message', '固定安排时间重叠')}")
        lines.append("")
    for day in output.get("days", []):
        if not isinstance(day, dict):
            continue
        lines.extend([f"## {day.get('date', '')} · {day.get('theme', '')}", ""])
        for item in day.get("items", []):
            if not isinstance(item, dict):
                continue
            marker = "固定" if item.get("kind") == "fixed" else "安排"
            start = _clock_text(item.get("startAt"))
            end = _clock_text(item.get("endAt"))
            lines.append(f"- **{start}–{end} {item.get('name', '')}** · {marker}")
            place = item.get("place") or {}
            location = (
                place.get("formatted_address") if isinstance(place, dict) else None
            ) or item.get("location")
            if location:
                lines.append(f"  {location}")
            if isinstance(place, dict) and place.get("map_url"):
                lines.append(f"  [在 Apple 地图中打开]({place['map_url']})")
            if item.get("notes"):
                lines.append(f"  {item['notes']}")
        lines.append("")
    checklist = output.get("checklist") or []
    if checklist:
        lines.extend(["## 出发前待办", ""])
        lines.extend(f"- [ ] {item}" for item in checklist)
    return "\n".join(lines).strip()


def business_trip_calendar_payload(
    output: dict[str, object], requested: BusinessTripInput
) -> dict[str, object]:
    zone = ZoneInfo(requested.time_zone)
    commitments = {item.id: item for item in requested.commitments}
    events: list[dict[str, object]] = []
    for day in output.get("days", []):
        if not isinstance(day, dict):
            continue
        for item in day.get("items", []):
            if not isinstance(item, dict):
                continue
            start = _as_datetime(item.get("startAt"))
            end = _as_datetime(item.get("endAt"))
            if start is None or end is None:
                continue
            place = item.get("place") or {}
            location = (
                place.get("formatted_address") if isinstance(place, dict) else None
            ) or item.get("location") or item.get("placeName")
            source = commitments.get(str(item.get("sourceCommitmentId") or ""))
            notes = str(item.get("notes") or "").strip()
            if source and source.confirmation_code:
                notes = "\n".join(
                    value for value in (notes, f"确认号：{source.confirmation_code}") if value
                )
            event: dict[str, object] = {
                "title": item.get("name"),
                "startAt": start.isoformat(),
                "endAt": end.isoformat(),
                "startLocal": start.astimezone(zone).strftime("%Y-%m-%dT%H:%M:%S"),
                "endLocal": end.astimezone(zone).strftime("%Y-%m-%dT%H:%M:%S"),
                "location": location,
                "url": place.get("map_url") if isinstance(place, dict) else None,
                "notes": notes or None,
            }
            if requested.add_calendar_alerts:
                event["alertsBeforeMinutes"] = _alert_offsets(
                    source.kind if source else str(item.get("kind") or "other")
                )
            events.append(event)
    return {
        "timeZoneMode": "destination",
        "timeZone": requested.time_zone,
        "destination": requested.destination,
        "events": events,
    }


def _alert_offsets(kind: str) -> list[int]:
    return {
        "flight": [180],
        "hotel": [120],
        "meeting": [30],
        "transport": [60],
        "preparation": [30],
    }.get(kind, [30])


def _as_datetime(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _clock_text(value: object) -> str:
    parsed = _as_datetime(value)
    return parsed.strftime("%H:%M") if parsed else ""
