"""Qwen provider transport and response validation."""

import httpx

from app.api.protocol import ChatRequest, ToolResultSubmission
from app.core.config import Settings
from app.providers.base import ProviderError, ProviderToolCallContext, ToolCallContextSink
from app.providers.qwen_messages import (
    continuation_payload,
    initial_messages,
    streaming_payload,
)
from app.providers.qwen_stream import QwenReplyStream
from app.tools.catalog import ModelToolCatalog


class UpstreamError(ProviderError):
    """Provider-specific public error retained for Qwen callers."""


class QwenProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        catalog: ModelToolCatalog | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=120)
        self._owns_client = client is None
        self._catalog = catalog or ModelToolCatalog()

    async def open_reply(
        self,
        request: ChatRequest,
        *,
        on_tool_call: ToolCallContextSink | None = None,
    ) -> QwenReplyStream:
        messages = initial_messages(request)
        upstream_request = self._client.build_request(
            "POST",
            self._endpoint,
            headers=self._headers(accept_stream=True),
            json=streaming_payload(self._settings.model, messages, self._catalog),
        )
        response = await self._send(upstream_request, stream=True)
        if "text/event-stream" not in response.headers.get("content-type", ""):
            await response.aclose()
            raise UpstreamError(502, "Qwen returned a non-streaming response")
        return QwenReplyStream(response, self._catalog, messages, on_tool_call)

    async def continue_reply(
        self,
        context: ProviderToolCallContext,
        result: ToolResultSubmission,
    ) -> str:
        if context.provider != "qwen":
            raise UpstreamError(500, "Tool call context belongs to another provider")
        messages = context.state.get("messages")
        tool_name = context.state.get("toolName")
        if not isinstance(messages, list) or not isinstance(tool_name, str):
            raise UpstreamError(500, "Stored Qwen Tool call context is invalid")

        upstream_request = self._client.build_request(
            "POST",
            self._endpoint,
            headers=self._headers(),
            json=continuation_payload(
                self._settings.model, messages, context, result, self._catalog
            ),
        )
        response = await self._send(upstream_request)
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise UpstreamError(502, "Qwen returned an invalid continuation") from error
        if not isinstance(content, str) or not content.strip():
            raise UpstreamError(502, "Qwen returned an empty continuation")
        return content.strip()

    @property
    def _endpoint(self) -> httpx.URL:
        return httpx.URL(self._settings.base_url).join("chat/completions")

    def _headers(self, *, accept_stream: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }
        if accept_stream:
            headers["Accept"] = "text/event-stream"
        return headers

    async def _send(
        self, request: httpx.Request, *, stream: bool = False
    ) -> httpx.Response:
        try:
            response = await self._client.send(request, stream=stream)
        except httpx.RequestError as error:
            raise UpstreamError(502, "Unable to reach Qwen") from error
        if response.is_success:
            return response
        body = (await response.aread()).decode(errors="replace")[:2_000]
        await response.aclose()
        raise UpstreamError(response.status_code, body or response.reason_phrase)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
