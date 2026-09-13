"""Server-executed long-task endpoints."""

from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.http import error_response, read_body, validation_message
from app.api.protocol import DeviceToolResultSubmission, PROTOCOL_VERSION
from app.core.config import Settings
from app.tasks.models import DeviceToolConflictError, ServerTaskCreate
from app.tasks.store import ServerTaskStore

router = APIRouter()


@router.post("/v1/conversations/{conversation_id}/tasks", status_code=201)
async def create_server_task(conversation_id: UUID, request: Request):
    settings: Settings = request.app.state.settings
    body_or_error = await read_body(request, settings.max_request_bytes)
    if isinstance(body_or_error, JSONResponse):
        return body_or_error
    try:
        task_request = ServerTaskCreate.model_validate_json(body_or_error)
    except ValidationError as error:
        return error_response(400, "invalid_request", validation_message(error))
    store: ServerTaskStore = request.app.state.server_task_store
    task = await store.create(conversation_id, task_request)
    return task.model_dump(mode="json", by_alias=True)


@router.get("/v1/conversations/{conversation_id}/tasks")
async def list_server_tasks(conversation_id: UUID, request: Request):
    store: ServerTaskStore = request.app.state.server_task_store
    tasks = await store.list(conversation_id)
    return {
        "tasks": [task.model_dump(mode="json", by_alias=True) for task in tasks],
        "protocolVersion": PROTOCOL_VERSION,
    }


@router.get("/v1/tasks/{task_id}")
async def get_server_task(task_id: UUID, request: Request):
    store: ServerTaskStore = request.app.state.server_task_store
    task = await store.get(task_id)
    if task is None:
        return error_response(404, "task_not_found", "Task does not exist")
    return task.model_dump(mode="json", by_alias=True)


@router.post("/v1/tasks/{task_id}/confirm")
async def confirm_server_task(task_id: UUID, request: Request):
    store: ServerTaskStore = request.app.state.server_task_store
    task = await store.confirm(task_id)
    if task is None:
        return error_response(404, "task_not_found", "Task does not exist")
    return task.model_dump(mode="json", by_alias=True)


@router.post("/v1/tasks/{task_id}/cancel")
async def cancel_server_task(task_id: UUID, request: Request):
    store: ServerTaskStore = request.app.state.server_task_store
    task = await store.cancel(task_id)
    if task is None:
        return error_response(404, "task_not_found", "Task does not exist")
    return task.model_dump(mode="json", by_alias=True)


@router.get("/v1/tasks/{task_id}/device-tools/pending")
async def get_pending_device_tool(task_id: UUID, request: Request):
    store: ServerTaskStore = request.app.state.server_task_store
    task = await store.get(task_id)
    if task is None:
        return error_response(404, "task_not_found", "Task does not exist")
    call = await store.get_pending_device_tool(task_id)
    if call is None:
        return Response(status_code=204)
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "taskId": str(call.task_id),
        "toolCallId": call.tool_call_id,
        "toolName": call.tool_name,
        "arguments": call.arguments,
    }


@router.post("/v1/tasks/{task_id}/device-tools/results")
async def submit_device_tool_result(task_id: UUID, request: Request):
    settings: Settings = request.app.state.settings
    body_or_error = await read_body(request, settings.max_request_bytes)
    if isinstance(body_or_error, JSONResponse):
        return body_or_error
    try:
        submission = DeviceToolResultSubmission.model_validate_json(body_or_error)
    except ValidationError as error:
        return error_response(400, "invalid_request", validation_message(error))
    store: ServerTaskStore = request.app.state.server_task_store
    try:
        call = await store.submit_device_tool_result(
            task_id,
            submission.tool_call_id,
            result=submission.result,
            error_code=submission.error.code if submission.error else None,
            error_message=submission.error.message if submission.error else None,
        )
    except DeviceToolConflictError as error:
        return error_response(409, "device_tool_result_conflict", str(error))
    if call is None:
        return error_response(
            404, "device_tool_not_found", "Device Tool call does not exist"
        )
    return {
        "accepted": True,
        "toolCallId": call.tool_call_id,
        "status": call.status,
    }
