"""Phone-executed Gmail and Drive bridge tests."""

import asyncio
from uuid import uuid4

from app.tasks.models import ServerTaskCreate
from app.tasks.stores.memory import InMemoryServerTaskStore
from app.tools.business_trip.device_google import DeviceGoogleWorkspaceClient


async def make_task(store: InMemoryServerTaskStore):
    return await store.create(
        uuid4(),
        ServerTaskCreate(
            capability="business-trip.plan",
            title="整理商务出差",
            input={},
        ),
    )


async def wait_for_pending(store, task_id):
    for _ in range(30):
        pending = await store.get_pending_device_tool(task_id)
        if pending is not None:
            return pending
        await asyncio.sleep(0.01)
    raise AssertionError("Device Tool was not queued")


async def test_gmail_bridge_uses_fixed_query_and_validates_messages(
    anyio_backend,
) -> None:
    store = InMemoryServerTaskStore()
    task = await make_task(store)
    client = DeviceGoogleWorkspaceClient(
        store, timeout_seconds=2, poll_seconds=0.01
    )
    search = asyncio.create_task(client.search_gmail(
        "after:2026/09/01 上海 出差",
        10,
        task_id=task.id,
        tool_call_id="gmail-1",
    ))
    pending = await wait_for_pending(store, task.id)

    assert pending.tool_name == "google.gmail.search"
    assert pending.arguments == {
        "query": "after:2026/09/01 上海 出差",
        "maxResults": 10,
    }
    await store.submit_device_tool_result(
        task.id,
        "gmail-1",
        result={
            "messages": [{
                "id": "message-1",
                "threadId": "thread-1",
                "subject": "上海机票确认",
                "sender": "airline@example.com",
                "date": "2026-09-20",
                "snippet": "出票成功",
                "bodyText": "MU5101 10 月 1 日 09:00 起飞",
            }],
            "source": "gmail-api-device",
        },
        error_code=None,
        error_message=None,
    )

    messages = await search
    assert messages[0].id == "message-1"
    assert messages[0].subject == "上海机票确认"


async def test_drive_bridge_requires_verified_upload_receipt(anyio_backend) -> None:
    store = InMemoryServerTaskStore()
    task = await make_task(store)
    client = DeviceGoogleWorkspaceClient(
        store, timeout_seconds=2, poll_seconds=0.01
    )
    upload = asyncio.create_task(client.upload_drive_text(
        name="上海商务出差计划.pdf",
        content="# 上海商务出差计划",
        mime_type="application/pdf",
        folder_id="folder-1",
        task_id=task.id,
        tool_call_id="drive-1",
    ))
    pending = await wait_for_pending(store, task.id)

    assert pending.tool_name == "google.drive.upload-text"
    assert pending.arguments["idempotencyKey"] == str(task.id)
    await store.submit_device_tool_result(
        task.id,
        "drive-1",
        result={
            "fileId": "file-1",
            "name": "上海商务出差计划.pdf",
            "webViewLink": "https://drive.google.com/file/d/file-1/view",
            "verified": True,
        },
        error_code=None,
        error_message=None,
    )

    receipt = await upload
    assert receipt["verified"] is True
