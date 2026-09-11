from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.config import Settings, load_settings
from app.protocol import ChatRequest
from app.providers.base import ModelProvider, ProviderError
from app.providers.qwen import QwenProvider


def create_app(
    *,
    settings: Settings | None = None,
    provider: ModelProvider | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved_settings = settings or load_settings()
        resolved_provider = provider or QwenProvider(resolved_settings)
        app.state.settings = resolved_settings
        app.state.provider = resolved_provider
        try:
            yield
        finally:
            await resolved_provider.close()

    application = FastAPI(
        title="WellPhone AI Backend",
        version="0.2.0",
        lifespan=lifespan,
    )

    @application.get("/health")
    async def health(request: Request) -> dict[str, str]:
        active_settings: Settings = request.app.state.settings
        return {
            "status": "ok",
            "model": active_settings.model,
            "protocolVersion": "1.0",
        }

    @application.post("/v1/conversations/{conversation_id}/messages")
    async def create_message(conversation_id: UUID, request: Request):
        del conversation_id
        active_settings: Settings = request.app.state.settings
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError:
                return _error(400, "invalid_request", "Content-Length is invalid")
            if declared_length > active_settings.max_request_bytes:
                return _error(413, "request_too_large", "Request body is too large")

        body = await request.body()
        if len(body) > active_settings.max_request_bytes:
            return _error(413, "request_too_large", "Request body is too large")
        try:
            chat_request = ChatRequest.model_validate_json(body)
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

    return application


def _validation_message(error: ValidationError) -> str:
    first = error.errors(include_url=False)[0]
    return str(first.get("msg", "Request body is invalid"))


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
    )


app = create_app()
