"""Device task-checkpoint endpoint."""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.http import error_response, read_body, validation_message
from app.api.protocol import PROTOCOL_VERSION, TaskCheckpointSubmission
from app.core.config import Settings
from app.tasks.checkpoints.models import TaskCheckpointConflictError
from app.tasks.checkpoints.repository import TaskCheckpointStore

router = APIRouter()


@router.post("/v1/conversations/{conversation_id}/task-checkpoints")
async def submit_task_checkpoint(conversation_id: UUID, request: Request):
    settings: Settings = request.app.state.settings
    body_or_error = await read_body(request, settings.max_request_bytes)
    if isinstance(body_or_error, JSONResponse):
        return body_or_error
    try:
        checkpoint = TaskCheckpointSubmission.model_validate_json(body_or_error)
    except ValidationError as error:
        return error_response(400, "invalid_request", validation_message(error))

    store: TaskCheckpointStore = request.app.state.task_checkpoint_store
    try:
        recorded = await store.record(conversation_id, checkpoint)
    except TaskCheckpointConflictError as error:
        return error_response(409, "task_checkpoint_conflict", str(error))
    return {
        "accepted": True,
        "duplicate": recorded.duplicate,
        "applied": recorded.applied,
        "currentRevision": recorded.current_revision,
        "gap": recorded.gap,
        "protocolVersion": PROTOCOL_VERSION,
    }
