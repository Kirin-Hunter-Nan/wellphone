"""Travel planning graph and tool tests."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest

from app.agent.loop import (
    AgentLoopEngine,
    AgentLoopRepeatedFailure,
    AgentLoopTaskHandler,
    AgentLoopToolRegistry,
    InMemoryAgentLoopJournal,
    LoopModelTurn,
    LoopToolCall,
)
from app.tasks.jobs import ServerTask
from app.tools.travel.planning import (
    AppleMapsPlace,
    ItinerarySubmitTool,
    PlacesSearchTool,
    make_travel_profile,
)


class FakeMaps:
    async def search(
        self, query: str, destination: str, language: str = "zh-CN"
    ) -> AppleMapsPlace:
        return AppleMapsPlace(
            name=query,
            formatted_address=f"{destination}·{query}",
            latitude=31.23,
            longitude=121.47,
            map_url="https://maps.apple.com/?q=test",
            verified=True,
        )


class ScriptedLoopModel:
    def __init__(self, turns: list[LoopModelTurn]) -> None:
        self._turns = turns
        self.messages_seen: list[list[dict[str, object]]] = []

    async def next_turn(self, messages, tool_definitions) -> LoopModelTurn:
        self.messages_seen.append(list(messages))
        return self._turns.pop(0)

    async def close(self) -> None:
        pass


def tool_turn(call_id: str, name: str, arguments: dict[str, object]) -> LoopModelTurn:
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


async def test_generic_loop_uses_observations_revises_and_builds_travel_artifacts(
    anyio_backend,
) -> None:
    plan = {
        "title": "上海一日慢游",
        "overview": "博物馆与咖啡体验",
        "timeZone": "Asia/Shanghai",
        "days": [{
            "date": "2026-10-01",
            "theme": "城市漫步",
            "items": [{
                "time": "09:30",
                "name": "上海博物馆",
                "durationMinutes": 120,
                "notes": "提前预约",
            }],
        }],
    }
    model = ScriptedLoopModel([
        # First submission is rejected because the place has not been searched.
        tool_turn("submit_1", "itinerary_submit", {
            "plan": json.dumps(plan, ensure_ascii=False),
        }),
        tool_turn("search_1", "places_search", {
            "query": "上海博物馆", "destination": "上海",
        }),
        tool_turn("submit_2", "itinerary_submit", {
            "plan": json.dumps(plan, ensure_ascii=False),
        }),
    ])
    journal = InMemoryAgentLoopJournal()
    engine = AgentLoopEngine(
        model,
        AgentLoopToolRegistry([
            PlacesSearchTool(FakeMaps()),  # type: ignore[arg-type]
            ItinerarySubmitTool(),
        ]),
        journal,
    )
    handler = AgentLoopTaskHandler(engine, [make_travel_profile()])
    now = datetime.now(timezone.utc)
    task = ServerTask(
        id=uuid4(), conversationId=uuid4(), capability="travel.plan",
        title="规划上海旅行",
        input={
            "destination": "上海", "startDate": "2026-10-01",
            "endDate": "2026-10-01", "preferences": ["博物馆", "咖啡"],
            "pace": "relaxed",
        },
        status="running", phase="starting", progress=0, requiresConfirmation=True,
        attemptCount=1, createdAt=now, updatedAt=now,
    )
    progress: list[str] = []
    progress_details: list[str] = []

    async def report(phase, value, detail, steps, current_step) -> None:
        progress.append(phase)
        progress_details.append(detail)

    outcome = await handler.run(task, report)
    events = await journal.load(task.id)

    assert len(model.messages_seen) == 3
    assert "needs_revision" in model.messages_seen[1][-1]["content"]
    assert model.messages_seen[2][-1]["name"] == "places_search"
    assert len(outcome.artifacts) == 3
    assert {item.kind for item in outcome.artifacts} == {"itinerary", "text", "json"}
    assert "Apple 地图" in outcome.artifacts[1].payload
    assert outcome.artifacts[2].payload["events"][0]["title"] == "上海博物馆"
    assert [event.event_type for event in events].count("model.turn") == 3
    assert [event.event_type for event in events].count("tool.result") == 3
    assert progress[-1] == "finalizing"
    assert any("第 1/12 轮" in detail for detail in progress_details)


async def test_generic_loop_resumes_a_persisted_pending_tool_call(
    anyio_backend,
) -> None:
    plan = {
        "title": "上海一日慢游",
        "overview": "博物馆体验",
        "timeZone": "Asia/Shanghai",
        "days": [{
            "date": "2026-10-01",
            "theme": "城市漫步",
            "items": [{
                "time": "09:30",
                "name": "上海博物馆",
                "durationMinutes": 120,
            }],
        }],
    }
    model = ScriptedLoopModel([
        tool_turn("submit_after_restart", "itinerary_submit", {"plan": plan}),
    ])
    journal = InMemoryAgentLoopJournal()
    engine = AgentLoopEngine(
        model,
        AgentLoopToolRegistry([
            PlacesSearchTool(FakeMaps()),  # type: ignore[arg-type]
            ItinerarySubmitTool(),
        ]),
        journal,
    )
    handler = AgentLoopTaskHandler(engine, [make_travel_profile()])
    now = datetime.now(timezone.utc)
    task = ServerTask(
        id=uuid4(), conversationId=uuid4(), capability="travel.plan",
        title="规划上海旅行",
        input={
            "destination": "上海", "startDate": "2026-10-01",
            "endDate": "2026-10-01", "pace": "relaxed",
        },
        status="running", phase="starting", progress=0, requiresConfirmation=True,
        attemptCount=2, createdAt=now, updatedAt=now,
    )
    pending_turn = tool_turn(
        "search_before_restart", "places_search",
        {"query": "上海博物馆", "destination": "上海"},
    )
    await journal.append(task.id, "model.turn", {"message": pending_turn.message})

    async def report(phase, value, detail, steps, current_step) -> None:
        pass

    outcome = await handler.run(task, report)
    events = await journal.load(task.id)

    assert len(model.messages_seen) == 1
    assert model.messages_seen[0][-1]["tool_call_id"] == "search_before_restart"
    assert outcome.summary == "上海一日慢游已完成，共规划 1 天。"
    assert [event.event_type for event in events].count("tool.result") == 2


async def test_langgraph_stops_after_three_identical_tool_failures(
    anyio_backend,
) -> None:
    bad_arguments = {"plan": "not-json"}
    model = ScriptedLoopModel([
        tool_turn("bad_1", "itinerary_submit", bad_arguments),
        tool_turn("bad_2", "itinerary_submit", bad_arguments),
        tool_turn("bad_3", "itinerary_submit", bad_arguments),
    ])
    journal = InMemoryAgentLoopJournal()
    handler = AgentLoopTaskHandler(
        AgentLoopEngine(
            model,
            AgentLoopToolRegistry([
                PlacesSearchTool(FakeMaps()),  # type: ignore[arg-type]
                ItinerarySubmitTool(),
            ]),
            journal,
        ),
        [make_travel_profile()],
    )
    now = datetime.now(timezone.utc)
    task = ServerTask(
        id=uuid4(), conversationId=uuid4(), capability="travel.plan",
        title="规划上海旅行",
        input={
            "destination": "上海", "startDate": "2026-10-01",
            "endDate": "2026-10-01", "pace": "relaxed",
        },
        status="running", phase="starting", progress=0, requiresConfirmation=True,
        attemptCount=1, createdAt=now, updatedAt=now,
    )

    async def report(phase, value, detail, steps, current_step) -> None:
        pass

    with pytest.raises(AgentLoopRepeatedFailure):
        await handler.run(task, report)

    events = await journal.load(task.id)
    assert len(model.messages_seen) == 3
    assert [event.event_type for event in events].count("tool.result") == 3
