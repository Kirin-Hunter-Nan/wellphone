"""Phone-executed EventKit calendar bridge tests."""

import asyncio
from datetime import datetime
from uuid import uuid4

from app.tasks.models import ServerTaskCreate
from app.tasks.stores.memory import InMemoryServerTaskStore
from app.tools.business_trip.device_calendar import DeviceCalendarClient


async def wait_for_pending(store, task_id):
    for _ in range(30):
        pending = await store.get_pending_device_tool(task_id)
        if pending is not None:
            return pending
        await asyncio.sleep(0.01)
    raise AssertionError("Device Tool was not queued")


async def test_calendar_bridge_uses_fixed_range_and_validates_events(
    anyio_backend,
) -> None:
    store = InMemoryServerTaskStore()
    task = await store.create(
        uuid4(),
        ServerTaskCreate(
            capability="business-trip.plan",
            title="整理商务出差",
            input={},
        ),
    )
    client = DeviceCalendarClient(
        store, timeout_seconds=2, poll_seconds=0.01
    )
    search = asyncio.create_task(client.search_events(
        datetime.fromisoformat("2026-09-16T00:00:00+08:00"),
        datetime.fromisoformat("2026-09-19T00:00:00+08:00"),
        100,
        task_id=task.id,
        tool_call_id="calendar-1",
    ))
    pending = await wait_for_pending(store, task.id)

    assert pending.tool_name == "calendar.events.search"
    assert pending.arguments == {
        "startAt": "2026-09-16T00:00:00+08:00",
        "endAt": "2026-09-19T00:00:00+08:00",
        "maxResults": 100,
    }
    await store.submit_device_tool_result(
        task.id,
        "calendar-1",
        result={
            "events": [{
                "id": "event-1#2026-09-17T06:30:00Z",
                "title": "产品团队周会",
                "startAt": "2026-09-17T06:30:00Z",
                "endAt": "2026-09-17T07:00:00Z",
                "calendarTitle": "工作",
                "isAllDay": False,
            }],
            "source": "eventkit-device",
        },
        error_code=None,
        error_message=None,
    )

    events = await search
    assert events[0].title == "产品团队周会"
    assert events[0].calendar_title == "工作"
