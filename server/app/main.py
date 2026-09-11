from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from contextvars import Context
from typing import AsyncIterator
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.chat_store import (
    ChatRequestStore,
    InMemoryChatRequestStore,
    PostgreSQLChatRequestStore,
)
from app.config import Settings, load_settings
from app.protocol import ChatRequest, PROTOCOL_VERSION, ToolResultSubmission
from app.providers.base import ModelProvider, ProviderError, ProviderToolCallContext
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
    chat_request_store: ChatRequestStore | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved_settings = settings or load_settings()
        resolved_provider = provider or QwenProvider(resolved_settings)
        resolved_result_store = result_store or PostgreSQLToolResultStore(
            resolved_settings.database_url
        )
        resolved_chat_request_store = chat_request_store
        if resolved_chat_request_store is None:
            resolved_chat_request_store = (
                InMemoryChatRequestStore()
                if result_store is not None
                else PostgreSQLChatRequestStore(resolved_settings.database_url)
            )
        await resolved_result_store.initialize()
        await resolved_chat_request_store.initialize()
        app.state.settings = resolved_settings
        app.state.provider = resolved_provider
        app.state.result_store = resolved_result_store
        app.state.chat_request_store = resolved_chat_request_store
        try:
            yield
        finally:
            await resolved_provider.close()
            await resolved_chat_request_store.close()
            await resolved_result_store.close()

    application = FastAPI(
        title="WellPhone AI Backend",
        version="0.4.0",
        lifespan=lifespan,
    )

    @application.get("/health")
    async def health(request: Request):
        active_settings: Settings = request.app.state.settings
        active_store: ToolResultStore = request.app.state.result_store
        active_chat_store: ChatRequestStore = request.app.state.chat_request_store
        if not await active_store.is_healthy() or not await active_chat_store.is_healthy():
            return _error(503, "database_unavailable", "Database is unavailable")
        return {
            "status": "ok",
            "model": active_settings.model,
            "protocolVersion": "1.0",
        }

    @application.post("/v1/conversations/{conversation_id}/messages")
    async def create_message(conversation_id: UUID, request: Request):
        active_settings: Settings = request.app.state.settings
        body_or_error = await _read_body(request, active_settings.max_request_bytes)
        if isinstance(body_or_error, JSONResponse):
            return body_or_error
        try:
            chat_request = ChatRequest.model_validate_json(body_or_error)
        except ValidationError as error:
            return _error(400, "invalid_request", _validation_message(error))

        active_provider: ModelProvider = request.app.state.provider
        active_store: ToolResultStore = request.app.state.result_store
        active_chat_store: ChatRequestStore = request.app.state.chat_request_store

        claim = await active_chat_store.claim(
            conversation_id,
            chat_request,
            lease_seconds=active_settings.chat_request_lease_seconds,
        )
        if claim.status == "conflict":
            return _error(
                409,
                "chat_request_conflict",
                "The requestId has already been used with different chat content",
            )
        if claim.status == "processing":
            response = _error(
                503,
                "chat_request_in_progress",
                "这轮回复正在生成，请稍后重试。",
            )
            response.headers["Retry-After"] = "2"
            return response
        if claim.status == "completed":
            async def replay_events() -> AsyncIterator[str]:
                for event in claim.events:
                    yield event

            return StreamingResponse(
                replay_events(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",
                    "X-WellPhone-Replayed": "true",
                },
            )

        async def save_tool_call_context(context: ProviderToolCallContext) -> None:
            await active_store.save_tool_call_context(conversation_id, context)

        try:
            stream = await active_provider.open_reply(
                chat_request,
                on_tool_call=save_tool_call_context,
            )
        except ProviderError:
            await active_chat_store.fail(
                conversation_id,
                chat_request.request_id,
                "model_upstream_error",
            )
            return _error(
                502,
                "model_upstream_error",
                "模型服务暂时不可用，请稍后重试。",
            )
        except BaseException:
            await _finish_store_operation(
                active_chat_store.fail(
                    conversation_id,
                    chat_request.request_id,
                    "provider_open_interrupted",
                )
            )
            raise

        async def persist_events() -> AsyncIterator[str]:
            events: list[str] = []
            try:
                async for event in stream.events():
                    events.append(event)
                    yield event
                await _finish_store_operation(
                    active_chat_store.complete(
                        conversation_id,
                        chat_request,
                        tuple(events),
                    )
                )
            except BaseException:
                await _finish_store_operation(
                    active_chat_store.fail(
                        conversation_id,
                        chat_request.request_id,
                        "response_stream_interrupted",
                    )
                )
                raise

        return StreamingResponse(
            persist_events(),
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
        context = await active_store.get_tool_call_context(
            conversation_id,
            submission.tool_call_id,
        )
        if context is not None and context.capability != submission.capability:
            return _error(
                409,
                "tool_capability_conflict",
                "Tool result capability does not match the original Tool call",
            )
        try:
            inserted = await active_store.record(conversation_id, submission)
        except ToolResultConflictError as error:
            return _error(409, "tool_result_conflict", str(error))

        if context is None:
            return {
                "accepted": True,
                "duplicate": not inserted,
                "continuationStatus": "unavailable",
                "protocolVersion": PROTOCOL_VERSION,
            }
        claim = await active_store.claim_continuation(
            conversation_id,
            submission.tool_call_id,
            lease_seconds=active_settings.continuation_lease_seconds,
        )
        if claim.status == "missing":
            return _error(
                500,
                "tool_context_missing",
                "Stored Tool call context is unavailable",
            )
        if claim.status == "processing":
            return _error(
                503,
                "continuation_in_progress",
                "设备操作结果已经保存，模型正在生成最终回复，请稍后重试。",
            )
        if claim.status == "completed":
            existing_reply = claim.assistant_reply
        else:
            active_provider: ModelProvider = request.app.state.provider
            try:
                existing_reply = await active_provider.continue_reply(context, submission)
                await active_store.save_assistant_reply(
                    conversation_id,
                    submission.tool_call_id,
                    existing_reply,
                )
            except ProviderError:
                await active_store.fail_continuation(
                    conversation_id,
                    submission.tool_call_id,
                    "model_upstream_error",
                )
                return _error(
                    502,
                    "model_upstream_error",
                    "设备操作结果已经保存，但模型暂时无法生成最终回复。",
                )
            except BaseException:
                await _finish_store_operation(
                    active_store.fail_continuation(
                        conversation_id,
                        submission.tool_call_id,
                        "continuation_interrupted",
                    )
                )
                raise
        if existing_reply is None:
            return _error(
                500,
                "assistant_reply_missing",
                "Completed Tool continuation has no assistant reply",
            )
        return {
            "accepted": True,
            "duplicate": not inserted,
            "continuationStatus": "completed",
            "assistantMessage": existing_reply,
            "protocolVersion": PROTOCOL_VERSION,
        }

    return application


async def _finish_store_operation(operation: Awaitable[None]) -> None:
    """Let persistence cleanup finish even when the request task is cancelled."""
    task = asyncio.create_task(operation, context=Context())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # Starlette cancels the whole request scope after a client disconnects.
        # The detached task must be allowed to commit after this coroutine exits.
        return


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
