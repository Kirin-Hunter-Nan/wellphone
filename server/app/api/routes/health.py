"""Service health endpoint."""

from fastapi import APIRouter, Request

from app.api.http import error_response
from app.conversations.store import ChatRequestStore
from app.core.config import Settings
from app.tasks.checkpoint_store import TaskCheckpointStore
from app.tasks.store import ServerTaskStore
from app.tool_results.store import ToolResultStore

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    settings: Settings = request.app.state.settings
    result_store: ToolResultStore = request.app.state.result_store
    chat_store: ChatRequestStore = request.app.state.chat_request_store
    checkpoint_store: TaskCheckpointStore = request.app.state.task_checkpoint_store
    task_store: ServerTaskStore = request.app.state.server_task_store
    if (
        not await result_store.is_healthy()
        or not await chat_store.is_healthy()
        or not await checkpoint_store.is_healthy()
        or not await task_store.is_healthy()
    ):
        return error_response(503, "database_unavailable", "Database is unavailable")
    return {
        "status": "ok",
        "model": settings.model,
        "protocolVersion": "1.0",
    }
