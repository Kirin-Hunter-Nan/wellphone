from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import httpx

from app.config import Settings
from app.jobs import ServerTask
from app.travel import AppleMapsPlace, TravelPlanHandler


class FakeMaps:
    async def search(self, query: str, destination: str, language: str = "zh-CN") -> AppleMapsPlace:
        return AppleMapsPlace(
            name=query,
            formatted_address=f"{destination}·{query}",
            latitude=31.23,
            longitude=121.47,
            map_url="https://maps.apple.com/?q=test",
            verified=True,
        )

    async def close(self) -> None:
        pass


async def test_travel_handler_creates_text_itinerary_and_calendar_artifacts(anyio_backend) -> None:
    def qwen_response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": """
        {"title":"上海两日慢游","overview":"步行与咖啡馆为主","days":[
          {"date":"2026-10-01","theme":"城市漫步","items":[
            {"time":"09:30","name":"上海博物馆","durationMinutes":120,"notes":"提前预约"}
          ]}
        ]}
        """}}]})

    settings = Settings(
        api_key="test", base_url="https://qwen.example/v1/", model="qwen-test"
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(qwen_response))
    handler = TravelPlanHandler(settings, client=client, maps=FakeMaps())  # type: ignore[arg-type]
    now = datetime.now(timezone.utc)
    task = ServerTask(
        id=uuid4(), conversationId=uuid4(), capability="travel.plan", title="规划上海旅行",
        input={
            "destination": "上海", "startDate": "2026-10-01", "endDate": "2026-10-02",
            "preferences": ["博物馆", "咖啡"], "pace": "relaxed",
        },
        status="running", phase="starting", progress=0, requiresConfirmation=True,
        attemptCount=1, createdAt=now, updatedAt=now,
    )
    progress: list[tuple[str, float]] = []

    async def report(phase, value, detail, steps, current_step) -> None:
        progress.append((phase, value))

    outcome = await handler.run(task, report)
    await handler.close()

    assert len(outcome.artifacts) == 3
    assert {item.kind for item in outcome.artifacts} == {"itinerary", "text", "json"}
    assert "Apple 地图" in outcome.artifacts[1].payload
    assert outcome.artifacts[2].payload["events"][0]["title"] == "上海博物馆"
    assert progress[-1][0] == "composing"
