"""Conversation message generation and replay endpoint."""

from typing import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.api.http import error_response, read_body, validation_message
from app.api.protocol import ChatRequest
from app.api.support import assistant_content, finish_store_operation
from app.conversations.repository import ChatRequestStore
from app.core.config import Settings
from app.providers.base import ModelProvider, ProviderError, ProviderToolCallContext
from app.tool_results.repository import ToolResultStore

router = APIRouter()


@router.post("/v1/conversations/{conversation_id}/messages")
async def create_message(conversation_id: UUID, request: Request):
    settings: Settings = request.app.state.settings
    body_or_error = await read_body(request, settings.max_request_bytes)
    if isinstance(body_or_error, JSONResponse):
        return body_or_error
    try:
        chat_request = ChatRequest.model_validate_json(body_or_error)
    except ValidationError as error:
        return error_response(400, "invalid_request", validation_message(error))

    provider: ModelProvider = request.app.state.provider
    result_store: ToolResultStore = request.app.state.result_store
    chat_store: ChatRequestStore = request.app.state.chat_request_store
    claim = await chat_store.claim(
        conversation_id,
        chat_request,
        lease_seconds=settings.chat_request_lease_seconds,
    )
    if claim.status == "conflict":
        return error_response(
            409,
            "chat_request_conflict",
            "The requestId has already been used with different chat content",
        )
    if claim.status == "processing":
        response = error_response(
            503, "chat_request_in_progress", "这轮回复正在生成，请稍后重试。"
        )
        response.headers["Retry-After"] = "2"
        return response
    if claim.status == "completed":
        async def replay_events() -> AsyncIterator[str]:
            for event in claim.events:
                yield event

        return _streaming_response(replay_events(), replayed=True)

    try:
        provider_request = await chat_store.contextualize(
            conversation_id,
            chat_request,
            max_messages=settings.conversation_context_messages,
            max_characters=settings.conversation_context_characters,
        )
    except Exception:
        await chat_store.fail(
            conversation_id, chat_request.request_id, "conversation_history_unavailable"
        )
        return error_response(
            503,
            "conversation_history_unavailable",
            "暂时无法读取对话上下文，请稍后重试。",
        )

    async def save_tool_call_context(context: ProviderToolCallContext) -> None:
        await result_store.save_tool_call_context(
            conversation_id,
            ProviderToolCallContext(
                tool_call_id=context.tool_call_id,
                capability=context.capability,
                provider=context.provider,
                state={**context.state, "_wellphoneRequestId": chat_request.request_id},
            ),
        )

    try:
        stream = await provider.open_reply(
            provider_request, on_tool_call=save_tool_call_context
        )
    except ProviderError:
        await chat_store.fail(
            conversation_id, chat_request.request_id, "model_upstream_error"
        )
        return error_response(
            502, "model_upstream_error", "模型服务暂时不可用，请稍后重试。"
        )
    except BaseException:
        await finish_store_operation(
            chat_store.fail(
                conversation_id, chat_request.request_id, "provider_open_interrupted"
            )
        )
        raise

    async def persist_events() -> AsyncIterator[str]:
        events: list[str] = []
        try:
            async for event in stream.events():
                events.append(event)
                yield event
            await finish_store_operation(
                chat_store.complete(
                    conversation_id,
                    chat_request,
                    tuple(events),
                    assistant_content(events),
                )
            )
        except BaseException:
            await finish_store_operation(
                chat_store.fail(
                    conversation_id, chat_request.request_id, "response_stream_interrupted"
                )
            )
            raise

    return _streaming_response(persist_events())


def _streaming_response(
    events: AsyncIterator[str], *, replayed: bool = False
) -> StreamingResponse:
    headers = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
    if replayed:
        headers["X-WellPhone-Replayed"] = "true"
    return StreamingResponse(events, media_type="text/event-stream", headers=headers)
