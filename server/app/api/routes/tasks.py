"""Server-executed long-task endpoints."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.http import error_response, read_body, validation_message
from app.api.protocol import PROTOCOL_VERSION
from app.core.config import Settings
from app.tasks.models import ServerTaskCreate
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
