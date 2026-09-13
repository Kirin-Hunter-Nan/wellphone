"""Queue-to-Agent-to-artifact deterministic business-trip pipeline test."""

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
from app.tools.business_trip.profile import make_business_trip_profile
from app.tools.business_trip.tools import (
    BusinessTripCommitmentsLockTool,
    BusinessTripSubmitTool,
    CalendarEventsSearchTool,
    GmailSearchTool,
    RoutesSearchTool,
)
from app.tools.travel.models import AppleMapsPlace
from app.tools.travel.tools import PlacesSearchTool


class UnusedMaps:
    async def search(self, query, destination, language="zh-CN", **_context):
        return AppleMapsPlace(
            name=query,
            map_url="https://maps.apple.com/?q=test",
            verified=True,
        )

    async def directions(self, *_args, **_kwargs):
        raise AssertionError("Routes should not be called without transfer items")


class UnusedGoogle:
    async def search_gmail(self, *_args, **_kwargs):
        raise AssertionError("Gmail should not be called for supplied commitments")


class UnusedCalendar:
    async def search_events(self, *_args, **_kwargs):
        raise AssertionError("Calendar should not be read without explicit authorization")


class BusinessTripModel:
    async def next_turn(self, messages, tool_definitions) -> LoopModelTurn:
        del messages, tool_definitions
        plan = {
            "title": "上海商务出差计划",
            "overview": "围绕客户会议安排",
            "timeZone": "Asia/Shanghai",
            "days": [{
                "date": "2026-10-01",
                "theme": "客户会议",
                "items": [{
                    "kind": "fixed",
                    "name": "客户方案会",
                    "startAt": "2026-10-01T14:00:00+08:00",
                    "endAt": "2026-10-01T15:00:00+08:00",
                    "sourceCommitmentId": "meeting-1",
                    "location": "陆家嘴",
                }],
            }],
            "checklist": ["准备演示材料"],
        }
        call = LoopToolCall("submit", "business_trip_submit", {"plan": plan})
        return LoopModelTurn(
            content=None,
            tool_calls=(call,),
            message={
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }],
            },
        )

    async def close(self) -> None:
        pass


async def test_business_trip_completes_with_durable_artifacts(anyio_backend) -> None:
    store = InMemoryServerTaskStore()
    journal = InMemoryAgentLoopJournal()
    handler = AgentLoopTaskHandler(
        AgentLoopEngine(
            BusinessTripModel(),
            AgentLoopToolRegistry([
                GmailSearchTool(UnusedGoogle()),  # type: ignore[arg-type]
                BusinessTripCommitmentsLockTool(),
                CalendarEventsSearchTool(UnusedCalendar()),  # type: ignore[arg-type]
                PlacesSearchTool(UnusedMaps()),  # type: ignore[arg-type]
                RoutesSearchTool(UnusedMaps()),  # type: ignore[arg-type]
                BusinessTripSubmitTool(),
            ]),
            journal,
        ),
        [make_business_trip_profile()],
    )
    runner = ServerTaskRunner(store, [handler], worker_id="business-trip-worker")
    created = await store.create(
        uuid4(),
        ServerTaskCreate(
            capability="business-trip.plan",
            title="整理上海商务出差",
            input={
                "destination": "上海",
                "startDate": "2026-10-01",
                "endDate": "2026-10-01",
                "timeZone": "Asia/Shanghai",
                "commitments": [{
                    "id": "meeting-1",
                    "kind": "meeting",
                    "title": "客户方案会",
                    "startAt": "2026-10-01T14:00:00+08:00",
                    "endAt": "2026-10-01T15:00:00+08:00",
                    "location": "陆家嘴",
                }],
            },
            toolCallId="call-business-trip",
        ),
    )

    assert await runner.run_once() is True
    completed = await store.get(created.id)

    assert completed is not None
    assert completed.status == "completed"
    assert completed.result_summary == (
        "上海商务出差计划已完成，整理 1 项固定安排，未发现时间冲突。"
    )
    assert len(completed.artifacts) == 4
    assert completed.artifacts[2].payload["checklist"] == ["准备演示材料"]
