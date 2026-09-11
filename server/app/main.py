from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.config import Settings, load_settings
from app.protocol import ChatRequest, PROTOCOL_VERSION, ToolResultSubmission
from app.providers.base import ModelProvider, ProviderError
from app.providers.qwen import QwenProvider
from app.postgres_store import (
    PostgreSQLToolResultStore,
    ToolResultConflictError,
    ToolResultStore,
)


def create_app(
    *,
    settings: Settings | None = None,
    provider: ModelProvider | None = None,
    result_store: ToolResultStore | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved_settings = settings or load_settings()
        resolved_provider = provider or QwenProvider(resolved_settings)
        resolved_result_store = result_store or PostgreSQLToolResultStore(
            resolved_settings.database_url
        )
        await resolved_result_store.initialize()
        app.state.settings = resolved_settings
        app.state.provider = resolved_provider
        app.state.result_store = resolved_result_store
        try:
            yield
        finally:
            await resolved_provider.close()
            await resolved_result_store.close()

    application = FastAPI(
        title="WellPhone AI Backend",
        version="0.2.0",
        lifespan=lifespan,
    )

    @application.get("/health")
    async def health(request: Request):
        active_settings: Settings = request.app.state.settings
        active_store: ToolResultStore = request.app.state.result_store
        if not await active_store.is_healthy():
            return _error(503, "database_unavailable", "Database is unavailable")
        return {
            "status": "ok",
            "model": active_settings.model,
            "protocolVersion": "1.0",
        }

    @application.post("/v1/conversations/{conversation_id}/messages")
    async def create_message(conversation_id: UUID, request: Request):
        del conversation_id
        active_settings: Settings = request.app.state.settings
        body_or_error = await _read_body(request, active_settings.max_request_bytes)
        if isinstance(body_or_error, JSONResponse):
            return body_or_error
        try:
            chat_request = ChatRequest.model_validate_json(body_or_error)
        except ValidationError as error:
            return _error(400, "invalid_request", _validation_message(error))

        active_provider: ModelProvider = request.app.state.provider
        try:
            stream = await active_provider.open_reply(chat_request)
        except ProviderError:
            return _error(
                502,
                "model_upstream_error",
                "模型服务暂时不可用，请稍后重试。",
            )

        return StreamingResponse(
            stream.events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @application.post("/v1/conversations/{conversation_id}/tool-results")
    async def submit_tool_result(conversation_id: UUID, request: Request):
        active_settings: Settings = request.app.state.settings
        body_or_error = await _read_body(request, active_settings.max_request_bytes)
        if isinstance(body_or_error, JSONResponse):
            return body_or_error
        try:
            submission = ToolResultSubmission.model_validate_json(body_or_error)
        except ValidationError as error:
            return _error(400, "invalid_request", _validation_message(error))

        active_store: ToolResultStore = request.app.state.result_store
        try:
            inserted = await active_store.record(conversation_id, submission)
        except ToolResultConflictError as error:
            return _error(409, "tool_result_conflict", str(error))

        return {
            "accepted": True,
            "duplicate": not inserted,
            "protocolVersion": PROTOCOL_VERSION,
        }

    return application


def _validation_message(error: ValidationError) -> str:
    first = error.errors(include_url=False)[0]
    return str(first.get("msg", "Request body is invalid"))


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
    )


async def _read_body(request: Request, max_request_bytes: int) -> bytes | JSONResponse:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError:
            return _error(400, "invalid_request", "Content-Length is invalid")
        if declared_length > max_request_bytes:
            return _error(413, "request_too_large", "Request body is too large")

    body = await request.body()
    if len(body) > max_request_bytes:
        return _error(413, "request_too_large", "Request body is too large")
    return body


app = create_app()
