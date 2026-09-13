"""Bridge Apple Maps search to native MapKit running headlessly on the phone."""

from __future__ import annotations

import asyncio
from time import monotonic
from uuid import UUID

from pydantic import ValidationError

from app.tasks.models import DeviceToolConflictError
from app.tasks.store import ServerTaskStore
from app.tools.travel.maps import AppleMapsSearchClient
from app.tools.travel.models import AppleMapsPlace, AppleMapsRoute


class DeviceMapKitSearchClient:
    def __init__(
        self,
        store: ServerTaskStore,
        *,
        fallback: AppleMapsSearchClient | None = None,
        timeout_seconds: float = 90,
        poll_seconds: float = 0.25,
    ) -> None:
        self._store = store
        self._fallback = fallback
        self._timeout_seconds = timeout_seconds
        self._poll_seconds = poll_seconds

    async def search(
        self,
        query: str,
        destination: str,
        language: str = "zh-CN",
        *,
        task_id: UUID | None = None,
        tool_call_id: str | None = None,
    ) -> AppleMapsPlace:
        if task_id is None or not tool_call_id:
            raise DeviceMapKitError("缺少设备地图回查所需的任务上下文。")
        await self._store.enqueue_device_tool(
            task_id,
            tool_call_id,
            "mapkit.local-search",
            {
                "query": query,
                "destination": destination,
                "language": language,
            },
        )
        deadline = monotonic() + self._timeout_seconds
        while monotonic() < deadline:
            call = await self._store.get_device_tool(task_id, tool_call_id)
            if call is None:
                raise DeviceMapKitError("设备地图回查请求丢失。")
            if call.status == "completed":
                try:
                    return AppleMapsPlace.model_validate(call.result)
                except ValidationError as error:
                    raise DeviceMapKitError(
                        "手机返回了无法解析的 MapKit 地点结果。"
                    ) from error
            if call.status == "failed":
                raise DeviceMapKitError(
                    call.error_message or "手机上的 MapKit 地点回查失败。"
                )
            await asyncio.sleep(self._poll_seconds)

        try:
            await self._store.submit_device_tool_result(
                task_id,
                tool_call_id,
                result=None,
                error_code="device_tool_timeout",
                error_message="等待手机执行 MapKit 地点回查超时。",
            )
        except DeviceToolConflictError:
            # The phone may have completed at the deadline boundary. Prefer its
            # persisted result over the timeout decision.
            call = await self._store.get_device_tool(task_id, tool_call_id)
            if call is not None and call.status == "completed":
                return AppleMapsPlace.model_validate(call.result)
        if self._fallback is not None:
            return await self._fallback.search(query, destination, language)
        raise DeviceMapKitError(
            "等待手机执行 MapKit 地点回查超时，请保持手机联网并允许任务继续运行。"
        )

    async def directions(
        self,
        origin: AppleMapsPlace,
        destination: AppleMapsPlace,
        departure_at: str,
        transport_type: str,
        *,
        task_id: UUID | None = None,
        tool_call_id: str | None = None,
    ) -> AppleMapsRoute:
        if task_id is None or not tool_call_id:
            raise DeviceMapKitError("缺少设备路线查询所需的任务上下文。")
        if origin.latitude is None or origin.longitude is None:
            raise DeviceMapKitError("路线起点缺少已核验坐标。")
        if destination.latitude is None or destination.longitude is None:
            raise DeviceMapKitError("路线终点缺少已核验坐标。")
        await self._store.enqueue_device_tool(
            task_id,
            tool_call_id,
            "mapkit.directions",
            {
                "originName": origin.name,
                "originLatitude": origin.latitude,
                "originLongitude": origin.longitude,
                "destinationName": destination.name,
                "destinationLatitude": destination.latitude,
                "destinationLongitude": destination.longitude,
                "departureAt": str(departure_at),
                "transportType": transport_type,
            },
        )
        result = await self._wait_for_result(task_id, tool_call_id)
        try:
            return AppleMapsRoute.model_validate(result)
        except ValidationError as error:
            raise DeviceMapKitError("手机返回了无法解析的 MapKit 路线结果。") from error

    async def _wait_for_result(
        self, task_id: UUID, tool_call_id: str
    ) -> dict[str, object]:
        deadline = monotonic() + self._timeout_seconds
        while monotonic() < deadline:
            call = await self._store.get_device_tool(task_id, tool_call_id)
            if call is None:
                raise DeviceMapKitError("设备地图请求丢失。")
            if call.status == "completed":
                if not isinstance(call.result, dict):
                    raise DeviceMapKitError("手机返回了空的 MapKit 结果。")
                return call.result
            if call.status == "failed":
                raise DeviceMapKitError(
                    call.error_message or "手机上的 MapKit 路线查询失败。"
                )
            await asyncio.sleep(self._poll_seconds)
        try:
            await self._store.submit_device_tool_result(
                task_id,
                tool_call_id,
                result=None,
                error_code="device_tool_timeout",
                error_message="等待手机执行 MapKit 路线查询超时。",
            )
        except DeviceToolConflictError:
            call = await self._store.get_device_tool(task_id, tool_call_id)
            if call is not None and call.status == "completed" and isinstance(call.result, dict):
                return call.result
        raise DeviceMapKitError(
            "等待手机执行 MapKit 路线查询超时，请保持手机联网并允许任务继续运行。"
        )


class DeviceMapKitError(RuntimeError):
    pass
