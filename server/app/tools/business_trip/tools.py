"""Atomic evidence collection and submission tools for business-trip planning."""

from datetime import datetime, time, timedelta
from pydantic import BaseModel
from zoneinfo import ZoneInfo

from app.agent.tools import LoopToolContext, LoopToolResult
from app.tools.business_trip.models import (
    BusinessTripCommitmentsLockArguments,
    BusinessTripInput,
    BusinessTripSubmitArguments,
    CalendarEventsSearchArguments,
    GmailSearchArguments,
)
from app.tools.business_trip.artifacts import render_business_trip_markdown
from app.tools.business_trip.reimbursement import render_reimbursement_markdown
from app.tools.business_trip.device_calendar import DeviceCalendarClient
from app.tools.business_trip.device_google import DeviceGoogleWorkspaceClient
from app.tools.business_trip.validation import (
    enrich_business_trip_plan,
    validate_business_trip_plan,
)
from app.tools.travel.device_maps import DeviceMapKitSearchClient
from app.tools.travel.models import (
    AppleMapsPlace,
    RouteSearchArguments,
)
from app.tools.travel.validation import matching_place


class BusinessTripSubmitTool:
    name = "business_trip_submit"
    description = (
        "Validate and submit the complete business-trip schedule. Include every fixed "
        "commitment exactly once without changing its title or time. If validation returns "
        "issues, revise only the affected items, search missing places, and submit again. "
        "For a fixed booking or meeting whose place remains ambiguous, preserve its original "
        "location and omit placeName; ambiguity must not block submission."
    )
    arguments_model = BusinessTripSubmitArguments

    def __init__(self, google: DeviceGoogleWorkspaceClient | None = None) -> None:
        self._google = google

    async def execute(
        self, arguments: BaseModel, context: LoopToolContext
    ) -> LoopToolResult:
        values = BusinessTripSubmitArguments.model_validate(arguments)
        requested = effective_business_trip_input(context)
        if requested.gmail_query is not None and not isinstance(
            context.state.get("lockedCommitments"), list
        ):
            return LoopToolResult({
                "ok": False,
                "error": {
                    "code": "gmail_commitments_not_locked",
                    "message": (
                        "Gmail 取证任务必须先完成 gmail_search 和 "
                        "business_trip_commitments_lock。"
                    ),
                },
            })
        if requested.check_calendar and not isinstance(
            context.state.get("calendarEvents"), list
        ):
            return LoopToolResult({
                "ok": False,
                "error": {
                    "code": "calendar_search_required",
                    "message": (
                        "任务要求检查现有日历，必须先完成 calendar_events_search。"
                    ),
                },
            })
        issues = validate_business_trip_plan(values.plan, requested, context.state)
        if issues:
            return LoopToolResult({
                "ok": False,
                "status": "needs_revision",
                "issues": issues,
            })
        output = enrich_business_trip_plan(values.plan, requested, context.state)
        if requested.upload_to_drive:
            if self._google is None:
                return LoopToolResult({
                    "ok": False,
                    "error": {
                        "code": "drive_unavailable",
                        "message": "Google Drive 设备连接器尚未配置。",
                    },
                })
            title = str(output.get("title") or f"{requested.destination}商务出差计划")
            receipt = await self._google.upload_drive_text(
                name=f"{title}.pdf",
                content=render_business_trip_markdown(output, requested),
                mime_type="application/pdf",
                folder_id=requested.drive_folder_id,
                task_id=context.task.id,
                tool_call_id=f"{context.tool_call_id or 'submit'}:drive:itinerary",
                idempotency_key=f"{context.task.id}:itinerary",
            )
            output["driveFile"] = receipt
            drive_files = [receipt]
            reimbursement = output.get("reimbursement")
            if (
                isinstance(reimbursement, dict)
                and reimbursement.get("receiptCount", 0) > 0
            ):
                reimbursement_receipt = await self._google.upload_drive_text(
                    name=f"{title}-报销清单.pdf",
                    content=render_reimbursement_markdown(reimbursement, requested),
                    mime_type="application/pdf",
                    folder_id=requested.drive_folder_id,
                    task_id=context.task.id,
                    tool_call_id=(
                        f"{context.tool_call_id or 'submit'}:drive:reimbursement"
                    ),
                    idempotency_key=f"{context.task.id}:reimbursement",
                )
                reimbursement["driveFile"] = reimbursement_receipt
                drive_files.append(reimbursement_receipt)
            output["driveFiles"] = drive_files
        return LoopToolResult(
            observation={"ok": True, "status": "accepted"},
            final_output=output,
        )


class GmailSearchTool:
    name = "gmail_search"
    description = (
        "Read only the Gmail query explicitly authorized in the task input. The query "
        "cannot be changed by the model. Use the returned message IDs as provenance when "
        "locking extracted commitments."
    )
    arguments_model = GmailSearchArguments

    def __init__(self, google: DeviceGoogleWorkspaceClient) -> None:
        self._google = google

    async def execute(
        self, arguments: BaseModel, context: LoopToolContext
    ) -> LoopToolResult:
        values = GmailSearchArguments.model_validate(arguments)
        requested = BusinessTripInput.model_validate(context.task.input)
        if requested.gmail_query is None:
            return LoopToolResult({
                "ok": False,
                "error": {
                    "code": "gmail_not_authorized",
                    "message": "任务输入没有授权 Gmail 搜索。",
                },
            })
        messages = await self._google.search_gmail(
            requested.gmail_query,
            values.max_results,
            task_id=context.task.id,
            tool_call_id=context.tool_call_id,
        )
        dumped = [item.model_dump(mode="json", by_alias=True) for item in messages]
        return LoopToolResult(
            observation={"ok": True, "messages": dumped},
            state_updates={
                "gmailMessages": {
                    item.id: value for item, value in zip(messages, dumped)
                }
            },
        )


class CalendarEventsSearchTool:
    name = "calendar_events_search"
    description = (
        "Read existing Apple Calendar events only when the task explicitly authorized "
        "calendar conflict checking. The date range is fixed by the task and cannot be "
        "changed by the model. Call this before building or submitting the itinerary."
    )
    arguments_model = CalendarEventsSearchArguments

    def __init__(self, calendar: DeviceCalendarClient) -> None:
        self._calendar = calendar

    async def execute(
        self, arguments: BaseModel, context: LoopToolContext
    ) -> LoopToolResult:
        values = CalendarEventsSearchArguments.model_validate(arguments)
        requested = BusinessTripInput.model_validate(context.task.input)
        if not requested.check_calendar:
            return LoopToolResult({
                "ok": False,
                "error": {
                    "code": "calendar_not_authorized",
                    "message": "任务输入没有授权读取现有日历。",
                },
            })
        cached = context.state.get("calendarEvents")
        if isinstance(cached, list):
            return LoopToolResult({
                "ok": True,
                "events": cached,
                "reused": True,
                "timeZone": requested.time_zone,
                "allDayEventsAreInformational": True,
            })

        zone = ZoneInfo(requested.time_zone)
        start_at = datetime.combine(requested.start_date, time.min, tzinfo=zone)
        end_at = datetime.combine(
            requested.end_date + timedelta(days=1), time.min, tzinfo=zone
        )
        events = await self._calendar.search_events(
            start_at,
            end_at,
            values.max_results,
            task_id=context.task.id,
            tool_call_id=context.tool_call_id,
        )
        dumped = []
        for item in events:
            value = item.model_dump(mode="json", by_alias=True)
            value["startAt"] = item.start_at.astimezone(zone).isoformat()
            value["endAt"] = item.end_at.astimezone(zone).isoformat()
            dumped.append(value)
        return LoopToolResult(
            observation={
                "ok": True,
                "events": dumped,
                "reused": False,
                "timeZone": requested.time_zone,
                "allDayEventsAreInformational": True,
            },
            state_updates={"calendarEvents": dumped},
        )


class RoutesSearchTool:
    name = "routes_search"
    description = (
        "Calculate a real MapKit route between two places already verified by places_search. "
        "Use it for every transfer in the business-trip plan, then copy route.id into the "
        "transfer item's routeId and preserve both endpoint place names."
    )
    arguments_model = RouteSearchArguments

    def __init__(self, maps: DeviceMapKitSearchClient) -> None:
        self._maps = maps

    async def execute(
        self, arguments: BaseModel, context: LoopToolContext
    ) -> LoopToolResult:
        values = RouteSearchArguments.model_validate(arguments)
        places = context.state.get("places")
        place_values = places if isinstance(places, dict) else {}
        origin_value = matching_place(values.origin_place_name, place_values)
        destination_value = matching_place(values.destination_place_name, place_values)
        if origin_value is None or origin_value.get("verified") is not True:
            return LoopToolResult({
                "ok": False,
                "error": {
                    "code": "route_origin_not_verified",
                    "message": f"请先用 places_search 核验路线起点：{values.origin_place_name}",
                },
            })
        if destination_value is None or destination_value.get("verified") is not True:
            return LoopToolResult({
                "ok": False,
                "error": {
                    "code": "route_destination_not_verified",
                    "message": (
                        f"请先用 places_search 核验路线终点：{values.destination_place_name}"
                    ),
                },
            })
        route = await self._maps.directions(
            AppleMapsPlace.model_validate(origin_value),
            AppleMapsPlace.model_validate(destination_value),
            values.departure_at.isoformat(),
            values.transport_type,
            task_id=context.task.id,
            tool_call_id=context.tool_call_id,
        )
        dumped = route.model_dump(mode="json", by_alias=True)
        return LoopToolResult(
            observation={"ok": True, "route": dumped},
            state_updates={"routes": {route.id: dumped}},
        )


class BusinessTripCommitmentsLockTool:
    name = "business_trip_commitments_lock"
    description = (
        "Lock fixed bookings and meetings extracted from Gmail. Every commitment must cite "
        "a sourceMessageId returned by gmail_search. Never infer missing dates or times. "
        "Gmail can contain evidence for other trips; commitments outside the requested trip "
        "dates are ignored deterministically."
    )
    arguments_model = BusinessTripCommitmentsLockArguments

    async def execute(
        self, arguments: BaseModel, context: LoopToolContext
    ) -> LoopToolResult:
        values = BusinessTripCommitmentsLockArguments.model_validate(arguments)
        messages = context.state.get("gmailMessages")
        if not isinstance(messages, dict):
            return LoopToolResult({
                "ok": False,
                "error": {
                    "code": "gmail_search_required",
                    "message": "请先调用 gmail_search。",
                },
            })
        requested = BusinessTripInput.model_validate(context.task.input)
        zone = ZoneInfo(requested.time_zone)
        in_scope = [
            item
            for item in values.commitments
            if requested.start_date
            <= item.start_at.astimezone(zone).date()
            <= requested.end_date
        ]
        ignored = [item.id for item in values.commitments if item not in in_scope]
        if not in_scope:
            return LoopToolResult({
                "ok": False,
                "error": {
                    "code": "no_in_scope_commitments",
                    "message": (
                        "没有可锁定的行程日期内固定安排；请只提取出差日期范围内的机票、"
                        "酒店和会议。"
                    ),
                    "ignoredCommitmentIds": ignored,
                },
            })
        missing = [
            item.id
            for item in in_scope
            if item.source_message_id is None or item.source_message_id not in messages
        ]
        if missing:
            return LoopToolResult({
                "ok": False,
                "error": {
                    "code": "invalid_commitment_provenance",
                    "message": "固定安排必须引用 gmail_search 返回的 sourceMessageId。",
                    "commitmentIds": missing,
                },
            })
        candidate = requested.model_copy(
            update={"commitments": [*requested.commitments, *in_scope]}
        )
        try:
            validated = BusinessTripInput.model_validate(
                candidate.model_dump(by_alias=True)
            )
        except Exception as error:
            return LoopToolResult({
                "ok": False,
                "error": {"code": "invalid_commitments", "message": str(error)},
            })
        locked = [
            item.model_dump(mode="json", by_alias=True)
            for item in validated.commitments
        ]
        observation: dict[str, object] = {
            "ok": True,
            "commitmentCount": len(locked),
        }
        if ignored:
            observation["ignoredOutOfScopeCommitmentIds"] = ignored
        return LoopToolResult(
            observation=observation,
            state_updates={"lockedCommitments": locked},
        )


def effective_business_trip_input(context: LoopToolContext) -> BusinessTripInput:
    requested = BusinessTripInput.model_validate(context.task.input)
    locked = context.state.get("lockedCommitments")
    if not isinstance(locked, list):
        return requested
    return BusinessTripInput.model_validate({
        **requested.model_dump(mode="json", by_alias=True),
        "commitments": locked,
    })
