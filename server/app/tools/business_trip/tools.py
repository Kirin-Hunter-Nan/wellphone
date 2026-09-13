"""Atomic Gmail collection and submission tools for business-trip planning."""

from pydantic import BaseModel

from app.agent.tools import LoopToolContext, LoopToolResult
from app.tools.business_trip.models import (
    BusinessTripCommitmentsLockArguments,
    BusinessTripInput,
    BusinessTripSubmitArguments,
    GmailSearchArguments,
)
from app.tools.business_trip.artifacts import render_business_trip_markdown
from app.tools.business_trip.device_google import DeviceGoogleWorkspaceClient
from app.tools.business_trip.validation import (
    enrich_business_trip_plan,
    validate_business_trip_plan,
)


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
                tool_call_id=f"{context.tool_call_id or 'submit'}:drive",
            )
            output["driveFile"] = receipt
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


class BusinessTripCommitmentsLockTool:
    name = "business_trip_commitments_lock"
    description = (
        "Lock fixed bookings and meetings extracted from Gmail. Every commitment must cite "
        "a sourceMessageId returned by gmail_search. Never infer missing dates or times."
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
        missing = [
            item.id
            for item in values.commitments
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
        requested = BusinessTripInput.model_validate(context.task.input)
        candidate = requested.model_copy(
            update={"commitments": [*requested.commitments, *values.commitments]}
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
        return LoopToolResult(
            observation={"ok": True, "commitmentCount": len(locked)},
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
