"""Queue-to-Agent-to-artifact deterministic travel pipeline test."""

import json
from uuid import uuid4

from app.agent.engine import AgentLoopEngine
from app.agent.handler import AgentLoopTaskHandler
from app.agent.journal import InMemoryAgentLoopJournal
from app.agent.model import LoopModelTurn, LoopToolCall
from app.agent.tools import AgentLoopToolRegistry
from app.tasks.models import ServerTaskCreate
from app.tasks.runner import ServerTaskRunner
from app.tasks.stores.memory import InMemoryServerTaskStore
from app.tools.travel.models import AppleMapsPlace
from app.tools.travel.profile import make_travel_profile
from app.tools.travel.tools import ItinerarySubmitTool, PlacesSearchTool


class RecordingMaps:
    def __init__(self) -> None:
        self.queries: list[tuple[str, str]] = []

    async def search(
        self, query: str, destination: str, language: str = "zh-CN"
    ) -> AppleMapsPlace:
        self.queries.append((query, destination))
        return AppleMapsPlace(
            name=query,
            formatted_address=f"{destination}市中心",
            latitude=31.23,
            longitude=121.47,
            map_url="https://maps.apple.com/?q=test",
            verified=True,
        )


class TravelModel:
    def __init__(self) -> None:
        self.turn_count = 0

    async def next_turn(self, messages, tool_definitions) -> LoopModelTurn:
        self.turn_count += 1
        if self.turn_count == 1:
            return turn("search", "places_search", {
                "query": "上海博物馆", "destination": "上海"
            })
        return turn("submit", "itinerary_submit", {"plan": {
            "title": "上海一日文化之旅",
            "overview": "博物馆主题行程",
            "timeZone": "Asia/Shanghai",
            "days": [{
                "date": "2026-10-01",
                "theme": "城市文化",
                "items": [{
                    "time": "09:00",
                    "name": "上海博物馆",
                    "durationMinutes": 120,
                    "notes": "提前预约",
                }],
            }],
        }})

    async def close(self) -> None:
        pass


def turn(call_id: str, name: str, arguments: dict[str, object]) -> LoopModelTurn:
    call = LoopToolCall(call_id, name, arguments)
    return LoopModelTurn(
        content=None,
        tool_calls=(call,),
        message={
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }],
        },
    )


async def test_confirmed_travel_task_completes_full_pipeline_once(
    anyio_backend,
) -> None:
    store = InMemoryServerTaskStore()
    journal = InMemoryAgentLoopJournal()
    maps = RecordingMaps()
    model = TravelModel()
    loop_handler = AgentLoopTaskHandler(
        AgentLoopEngine(
            model,
            AgentLoopToolRegistry([
                PlacesSearchTool(maps),  # type: ignore[arg-type]
                ItinerarySubmitTool(),
            ]),
            journal,
        ),
        [make_travel_profile()],
    )
    runner = ServerTaskRunner(
        store, [loop_handler], worker_id="integration-worker", max_attempts=3
    )
    created = await store.create(
        uuid4(),
        ServerTaskCreate(
            capability="travel.plan",
            title="规划上海旅行",
            input={
                "destination": "上海",
                "startDate": "2026-10-01",
                "endDate": "2026-10-01",
                "pace": "relaxed",
            },
            requiresConfirmation=True,
            toolCallId="call-travel",
        ),
    )

    assert await runner.run_once() is False
    await store.confirm(created.id)
    assert await runner.run_once() is True
    assert await runner.run_once() is False

    completed = await store.get(created.id)
    events = await journal.load(created.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.progress == 1
    assert completed.attempt_count == 1
    assert completed.result_summary == "上海一日文化之旅已完成，共规划 1 天。"
    assert [artifact.kind for artifact in completed.artifacts] == [
        "itinerary", "text", "json"
    ]
    assert model.turn_count == 2
    assert maps.queries == [("上海博物馆", "上海")]
    assert [event.event_type for event in events].count("model.turn") == 2
    assert [event.event_type for event in events].count("tool.result") == 2
