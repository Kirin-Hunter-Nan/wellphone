"""Travel result rendering and task artifact construction."""

from datetime import datetime, timedelta

from app.tasks.models import ArtifactDraft, ServerTask, TaskOutcome
from app.tools.travel.models import TravelPlanInput, TravelPlanOutput


def build_travel_outcome(task: ServerTask, output: dict[str, object]) -> TaskOutcome:
    travel = TravelPlanInput.model_validate(task.input)
    plan = TravelPlanOutput.model_validate(output).model_dump(mode="json", by_alias=True)
    markdown = render_markdown(plan, travel)
    calendar = calendar_payload(plan, travel)
    title = str(plan["title"])
    return TaskOutcome(
        summary=f"{title}已完成，共规划 {len(plan['days'])} 天。",
        artifacts=(
            ArtifactDraft(
                kind="itinerary",
                title=title,
                content_type="application/vnd.wellphone.itinerary+json",
                payload=plan,
            ),
            ArtifactDraft(
                kind="text",
                title=f"{title}（文本版）",
                content_type="text/markdown",
                payload=markdown,
            ),
            ArtifactDraft(
                kind="json",
                title=f"{title}（日历事件）",
                content_type="application/vnd.wellphone.calendar-events+json",
                payload=calendar,
            ),
        ),
    )


def render_markdown(plan: dict[str, object], travel: TravelPlanInput) -> str:
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


def calendar_payload(
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
