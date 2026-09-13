"""Durable bridge to the user's EventKit calendar on their phone."""

from __future__ import annotations

import asyncio
from datetime import datetime
from time import monotonic
from uuid import UUID

from pydantic import ValidationError

from app.tasks.models import DeviceToolConflictError
from app.tasks.store import ServerTaskStore
from app.tools.business_trip.models import CalendarEvent


class DeviceCalendarClient:
    def __init__(
        self,
        store: ServerTaskStore,
        *,
        timeout_seconds: float = 180,
        poll_seconds: float = 0.25,
    ) -> None:
        self._store = store
        self._timeout_seconds = timeout_seconds
        self._poll_seconds = poll_seconds

    async def search_events(
        self,
        start_at: datetime,
        end_at: datetime,
        max_results: int,
        *,
        task_id: UUID | None,
        tool_call_id: str | None,
    ) -> list[CalendarEvent]:
        result = await self._execute(
            "calendar.events.search",
            {
                "startAt": start_at.isoformat(),
                "endAt": end_at.isoformat(),
                "maxResults": max_results,
            },
            task_id=task_id,
            tool_call_id=tool_call_id,
        )
        events = result.get("events")
        if not isinstance(events, list):
            raise DeviceCalendarError("手机返回的日历结果缺少 events。")
        try:
            return [CalendarEvent.model_validate(item) for item in events]
        except ValidationError as error:
            raise DeviceCalendarError("手机返回了无法解析的日历事件。") from error

    async def _execute(
        self,
        tool_name: str,
        arguments: dict[str, object],
        *,
        task_id: UUID | None,
        tool_call_id: str | None,
    ) -> dict[str, object]:
        if task_id is None or not tool_call_id:
            raise DeviceCalendarError("缺少日历设备工具上下文。")
        await self._store.enqueue_device_tool(
            task_id, tool_call_id, tool_name, arguments
        )
        deadline = monotonic() + self._timeout_seconds
        while monotonic() < deadline:
            call = await self._store.get_device_tool(task_id, tool_call_id)
            if call is None:
                raise DeviceCalendarError("日历设备请求丢失。")
            if call.status == "completed":
                if not isinstance(call.result, dict):
                    raise DeviceCalendarError("手机返回了空的日历结果。")
                return call.result
            if call.status == "failed":
                raise DeviceCalendarError(
                    call.error_message or "手机上的日历读取失败。"
                )
            await asyncio.sleep(self._poll_seconds)

        try:
            await self._store.submit_device_tool_result(
                task_id,
                tool_call_id,
                result=None,
                error_code="device_tool_timeout",
                error_message="等待手机读取日历超时。",
            )
        except DeviceToolConflictError:
            call = await self._store.get_device_tool(task_id, tool_call_id)
            if (
                call is not None
                and call.status == "completed"
                and isinstance(call.result, dict)
            ):
                return call.result
        raise DeviceCalendarError(
            "等待手机读取日历超时，请保持手机联网并允许任务继续运行。"
        )


class DeviceCalendarError(RuntimeError):
    pass
