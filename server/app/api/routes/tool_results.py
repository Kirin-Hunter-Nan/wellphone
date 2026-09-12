"""Device Tool result submission and continuation endpoint."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.http import error_response, read_body, validation_message
from app.api.protocol import PROTOCOL_VERSION, ToolResultSubmission
from app.api.support import deterministic_tool_followup, finish_store_operation
from app.conversations.repository import ChatRequestStore
from app.core.config import Settings
from app.providers.base import ModelProvider, ProviderError
from app.tool_results.models import ToolResultConflictError
from app.tool_results.repository import ToolResultStore

router = APIRouter()


@router.post("/v1/conversations/{conversation_id}/tool-results")
async def submit_tool_result(conversation_id: UUID, request: Request):
    settings: Settings = request.app.state.settings
    body_or_error = await read_body(request, settings.max_request_bytes)
    if isinstance(body_or_error, JSONResponse):
        return body_or_error
    try:
        submission = ToolResultSubmission.model_validate_json(body_or_error)
    except ValidationError as error:
        return error_response(400, "invalid_request", validation_message(error))

    store: ToolResultStore = request.app.state.result_store
    chat_store: ChatRequestStore = request.app.state.chat_request_store
    context = await store.get_tool_call_context(conversation_id, submission.tool_call_id)
    if context is not None and context.capability != submission.capability:
        return error_response(
            409,
            "tool_capability_conflict",
            "Tool result capability does not match the original Tool call",
        )
    try:
        inserted = await store.record(conversation_id, submission)
    except ToolResultConflictError as error:
        return error_response(409, "tool_result_conflict", str(error))

    if context is None:
        return _response(inserted, continuation_status="unavailable")

    claim = await store.claim_continuation(
        conversation_id,
        submission.tool_call_id,
        lease_seconds=settings.continuation_lease_seconds,
    )
    if claim.status == "missing":
        return error_response(500, "tool_context_missing", "Stored Tool call context is unavailable")
    if claim.status == "processing":
        return error_response(
            503,
            "continuation_in_progress",
            "设备操作结果已经保存，模型正在生成最终回复，请稍后重试。",
        )

    if claim.status == "completed":
        assistant_reply = claim.assistant_reply
    else:
        assistant_reply = deterministic_tool_followup(submission)
        if assistant_reply is not None:
            await store.save_assistant_reply(
                conversation_id, submission.tool_call_id, assistant_reply
            )
        else:
            assistant_reply = await _continue_with_provider(
                request, conversation_id, submission, context, store
            )
            if isinstance(assistant_reply, JSONResponse):
                return assistant_reply

    if assistant_reply is None:
        return error_response(
            500, "assistant_reply_missing", "Completed Tool continuation has no assistant reply"
        )
    source_request_id = context.state.get("_wellphoneRequestId")
    if isinstance(source_request_id, str):
        await finish_store_operation(
            chat_store.save_assistant_reply(
                conversation_id, source_request_id, assistant_reply
            )
        )
    return _response(
        inserted, continuation_status="completed", assistant_message=assistant_reply
    )


async def _continue_with_provider(
    request: Request,
    conversation_id: UUID,
    submission: ToolResultSubmission,
    context,
    store: ToolResultStore,
):
    provider: ModelProvider = request.app.state.provider
    try:
        reply = await provider.continue_reply(context, submission)
        await store.save_assistant_reply(conversation_id, submission.tool_call_id, reply)
        return reply
    except ProviderError:
        await store.fail_continuation(
            conversation_id, submission.tool_call_id, "model_upstream_error"
        )
        return error_response(
            502,
            "model_upstream_error",
            "设备操作结果已经保存，但模型暂时无法生成最终回复。",
        )
    except BaseException:
        await finish_store_operation(
            store.fail_continuation(
                conversation_id, submission.tool_call_id, "continuation_interrupted"
            )
        )
        raise


def _response(
    inserted: bool,
    *,
    continuation_status: str,
    assistant_message: str | None = None,
) -> dict[str, object]:
    response: dict[str, object] = {
        "accepted": True,
        "duplicate": not inserted,
        "continuationStatus": continuation_status,
        "protocolVersion": PROTOCOL_VERSION,
    }
    if assistant_message is not None:
        response["assistantMessage"] = assistant_message
    return response
