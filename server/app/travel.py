from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from typing import Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import Settings
from app.jobs import ArtifactDraft, ServerTask, TaskOutcome


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

    async def search(self, query: str, destination: str, language: str = "zh-CN") -> AppleMapsPlace:
        fallback_url = "https://maps.apple.com/?" + urlencode({"q": f"{query} {destination}"})
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


class TravelPlanHandler:
    capability = "travel.plan"

    def __init__(
        self, settings: Settings, *, client: httpx.AsyncClient | None = None,
        maps: AppleMapsSearchClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=120)
        self._owns_client = client is None
        self._maps = maps or AppleMapsSearchClient(settings.apple_maps_token)

    async def run(self, task: ServerTask, report) -> TaskOutcome:
        travel = TravelPlanInput.model_validate(task.input)
        steps = ("理解旅行需求", "生成候选行程", "核对地点", "整理行程与日历")
        await report("understanding", 0.08, "正在整理日期、节奏和偏好", steps, 0)
        plan = await self._generate_plan(travel)
        await report("planning", 0.42, "候选行程已经生成", steps, 1)

        items = [item for day in plan.get("days", []) for item in day.get("items", [])]
        for index, item in enumerate(items):
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            place = await self._maps.search(name, travel.destination)
            item["place"] = place.model_dump(mode="json", by_alias=True)
            progress = 0.45 + (0.35 * (index + 1) / max(len(items), 1))
            await report("researching", progress, f"正在核对地点：{name}", steps, 2)

        await report("composing", 0.88, "正在生成文本和日历结构", steps, 3)
        markdown = _render_markdown(plan, travel)
        calendar = _calendar_payload(plan, travel)
        title = str(plan.get("title") or f"{travel.destination}旅行计划")
        return TaskOutcome(
            summary=f"{title}已完成，共规划 {len(plan.get('days', []))} 天。",
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

    async def _generate_plan(self, travel: TravelPlanInput) -> dict[str, object]:
        endpoint = httpx.URL(self._settings.base_url).join("chat/completions")
        response = await self._client.post(
            endpoint,
            headers={"Authorization": f"Bearer {self._settings.api_key}"},
            json={
                "model": self._settings.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是旅行规划器。只输出一个 JSON 对象，不要 Markdown。"
                            "结构必须是 {title,overview,days:[{date,theme,items:["
                            "{time,name,durationMinutes,notes}]}],timeZone}。timeZone 必须是目的地的 "
                            "IANA 时区。地点名称必须真实、具体；"
                            "同一天的地点应尽量相邻，时间必须为 HH:mm。不要虚构营业时间或票价。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(travel.model_dump(mode="json", by_alias=True), ensure_ascii=False),
                    },
                ],
                "response_format": {"type": "json_object"},
                "stream": False,
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        plan = _decode_json_object(content)
        if not isinstance(plan.get("days"), list):
            raise ValueError("模型没有返回有效的旅行日程")
        return plan

    async def close(self) -> None:
        await self._maps.close()
        if self._owns_client:
            await self._client.aclose()


def _decode_json_object(content: str) -> dict[str, object]:
    value = content.strip()
    if value.startswith("```"):
        value = value.removeprefix("```json").removeprefix("```")
        value = value.removesuffix("```").strip()
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object from the travel planner")
    return parsed


def _render_markdown(plan: dict[str, object], travel: TravelPlanInput) -> str:
    lines = [f"# {plan.get('title') or travel.destination + '旅行计划'}", "", str(plan.get("overview") or ""), ""]
    for day in plan.get("days", []):
        lines.extend([f"## {day.get('date', '')} · {day.get('theme', '')}", ""])
        for item in day.get("items", []):
            place = item.get("place") or {}
            lines.append(f"- **{item.get('time', '')} {item.get('name', '')}**（约 {item.get('durationMinutes', '')} 分钟）")
            if item.get("notes"):
                lines.append(f"  {item['notes']}")
            if place.get("map_url"):
                lines.append(f"  [在 Apple 地图中打开]({place['map_url']})")
        lines.append("")
    return "\n".join(lines).strip()


def _calendar_payload(plan: dict[str, object], travel: TravelPlanInput) -> dict[str, object]:
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
