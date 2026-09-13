"""Phone-executed MapKit bridge tests."""

import asyncio
from uuid import uuid4

from app.tasks.models import ServerTaskCreate
from app.tasks.stores.memory import InMemoryServerTaskStore
from app.tools.travel.device_maps import DeviceMapKitSearchClient


async def test_device_mapkit_search_waits_for_and_validates_phone_result(
    anyio_backend,
) -> None:
    store = InMemoryServerTaskStore()
    task = await store.create(
        uuid4(),
        ServerTaskCreate(
            capability="travel.plan",
            title="规划上海旅行",
            input={},
        ),
    )
    client = DeviceMapKitSearchClient(
        store,
        timeout_seconds=2,
        poll_seconds=0.01,
    )
    search = asyncio.create_task(client.search(
        "上海博物馆",
        "上海",
        task_id=task.id,
        tool_call_id="search_1",
    ))

    pending = None
    for _ in range(20):
        pending = await store.get_pending_device_tool(task.id)
        if pending is not None:
            break
        await asyncio.sleep(0.01)
    assert pending is not None
    assert pending.tool_name == "mapkit.local-search"
    assert pending.arguments["destination"] == "上海"

    await store.submit_device_tool_result(
        task.id,
        "search_1",
        result={
            "name": "上海博物馆（人民广场馆）",
            "formatted_address": "上海市黄浦区人民大道201号",
            "latitude": 31.2304,
            "longitude": 121.4737,
            "map_url": "https://maps.apple.com/place?place-id=test",
            "place_id": "test",
            "verified": True,
            "confidence": 0.96,
            "source": "mapkit-native",
        },
        error_code=None,
        error_message=None,
    )

    place = await search
    assert place.verified is True
    assert place.place_id == "test"
    assert place.source == "mapkit-native"


async def test_device_tool_result_is_idempotent(anyio_backend) -> None:
    store = InMemoryServerTaskStore()
    task = await store.create(
        uuid4(),
        ServerTaskCreate(capability="travel.plan", title="规划旅行", input={}),
    )
    await store.enqueue_device_tool(
        task.id,
        "search_1",
        "mapkit.local-search",
        {"query": "故宫", "destination": "北京"},
    )
    result = {
        "name": "故宫博物院",
        "map_url": "https://maps.apple.com/place?place-id=palace",
        "verified": True,
    }

    first = await store.submit_device_tool_result(
        task.id,
        "search_1",
        result=result,
        error_code=None,
        error_message=None,
    )
    duplicate = await store.submit_device_tool_result(
        task.id,
        "search_1",
        result=result,
        error_code=None,
        error_message=None,
    )

    assert first == duplicate
    assert await store.get_pending_device_tool(task.id) is None
