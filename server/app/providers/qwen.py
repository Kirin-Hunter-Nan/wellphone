from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import AsyncIterator
from uuid import uuid4

import httpx

from app.config import Settings
from app.providers.base import (
    ProviderError,
    ProviderToolCallContext,
    ToolCallContextSink,
)
from app.protocol import (
    ChatRequest,
    ToolResultSubmission,
    assistant_delta,
    encode_sse,
    response_completed,
    response_failed,
    response_started,
    tool_requested,
)
from app.tools.catalog import ModelToolCatalog, ToolCatalogError


class UpstreamError(ProviderError):
    """Retained as the provider-specific public error for Qwen callers."""


@dataclass(slots=True)
class _ToolCallAccumulator:
    tool_call_id: str = ""
    name: str = ""
    arguments: str = ""


class QwenReplyStream:
    def __init__(
        self,
        response: httpx.Response,
        catalog: ModelToolCatalog,
        provider_messages: list[dict[str, object]],
        on_tool_call: ToolCallContextSink | None,
    ) -> None:
        self._response = response
        self._catalog = catalog
        self._provider_messages = provider_messages
        self._on_tool_call = on_tool_call
        self._response_id = f"resp_{uuid4().hex}"

    async def events(self) -> AsyncIterator[str]:
        yield encode_sse(response_started(self._response_id))
        tool_calls: dict[int, _ToolCallAccumulator] = {}
        assistant_text = ""

        try:
            async for line in self._response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                if payload == "[DONE]":
                    try:
                        completed_calls = [
                            tool_calls[index]
                            for index in sorted(tool_calls)
                            if tool_calls[index].name
                        ]
                        if len(completed_calls) > 1:
                            yield encode_sse(response_failed(
                                self._response_id,
                                "parallel_tool_calls_unsupported",
                                "当前设备运行时一次只支持一个工具请求。",
                            ))
                            return
                        for accumulated in completed_calls:
                            normalized = self._catalog.normalize(
                                model_name=accumulated.name,
                                tool_call_id=accumulated.tool_call_id or f"call_{uuid4().hex}",
                                arguments_json=accumulated.arguments,
                            )
                            if self._on_tool_call is not None:
                                await self._on_tool_call(ProviderToolCallContext(
                                    tool_call_id=normalized.tool_call_id,
                                    capability=normalized.capability,
                                    provider="qwen",
                                    state={
                                        "messages": [
                                            *self._provider_messages,
                                            {
                                                "role": "assistant",
                                                "content": assistant_text or None,
                                                "tool_calls": [{
                                                    "id": normalized.tool_call_id,
                                                    "type": "function",
                                                    "function": {
                                                        "name": accumulated.name,
                                                        "arguments": accumulated.arguments,
                                                    },
                                                }],
                                            },
                                        ],
                                        "toolName": accumulated.name,
                                    },
                                ))
                            yield encode_sse(tool_requested(self._response_id, normalized))
                    except ToolCatalogError:
                        yield encode_sse(response_failed(
                            self._response_id,
                            "invalid_tool_request",
                            "模型返回了无法执行的工具请求。",
                        ))
                        return
                    except Exception:
                        yield encode_sse(response_failed(
                            self._response_id,
                            "tool_context_persistence_failed",
                            "工具请求暂时无法保存，请稍后重试。",
                        ))
                        return

                    yield encode_sse(response_completed(self._response_id))
                    return

                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    yield encode_sse(response_failed(
                        self._response_id,
                        "invalid_upstream_event",
                        "模型返回了无效的流事件。",
                    ))
                    return

                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        assistant_text += text
                        yield encode_sse(assistant_delta(self._response_id, text))

                    for tool_call in delta.get("tool_calls") or []:
                        index = tool_call.get("index", 0)
                        if not isinstance(index, int):
                            continue
                        accumulated = tool_calls.setdefault(index, _ToolCallAccumulator())
                        tool_call_id = tool_call.get("id")
                        if isinstance(tool_call_id, str) and tool_call_id:
                            accumulated.tool_call_id = tool_call_id
                        function = tool_call.get("function") or {}
                        name = function.get("name")
                        if isinstance(name, str) and name:
                            accumulated.name = name
                        arguments = function.get("arguments")
                        if isinstance(arguments, str):
                            accumulated.arguments += arguments

            yield encode_sse(response_failed(
                self._response_id,
                "upstream_stream_interrupted",
                "模型响应流意外中断。",
            ))
        finally:
            await self._response.aclose()


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
        endpoint = httpx.URL(self._settings.base_url).join("chat/completions")
        request_time = datetime.now(timezone.utc).isoformat()
        time_zone = request.device_context.time_zone
        provider_messages = [
            {
                "role": "system",
                "content": (
                    "You are WellPhone, an iPhone assistant. "
                    f"The current time is {request_time}. "
                    f"The user's IANA time zone is {time_zone}. "
                    "Use reminder_create only when the user explicitly asks to create "
                    "a reminder. Resolve relative dates to an absolute ISO 8601 "
                    "date-time with an explicit UTC offset. Never claim a reminder was "
                    "created: the iPhone app will ask for confirmation and report the result. "
                    "After receiving a Tool result, describe the outcome using only facts "
                    "explicitly present in result or error. If result includes dueAt and "
                    "timeZone, reproduce them as supplied; never infer, recalculate, or "
                    "contradict their time zone."
                ),
            },
            *request.provider_messages(),
        ]
        upstream_request = self._client.build_request(
            "POST",
            endpoint,
            headers={
                "Authorization": f"Bearer {self._settings.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            json={
                "model": self._settings.model,
                "messages": provider_messages,
                "tools": self._catalog.model_definitions(),
                "tool_choice": "auto",
                "parallel_tool_calls": False,
                "tool_stream": False,
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        )
        try:
            response = await self._client.send(upstream_request, stream=True)
        except httpx.RequestError as error:
            raise UpstreamError(502, "Unable to reach Qwen") from error
        if not response.is_success:
            body = (await response.aread()).decode(errors="replace")[:2_000]
            await response.aclose()
            raise UpstreamError(response.status_code, body or response.reason_phrase)
        if "text/event-stream" not in response.headers.get("content-type", ""):
            await response.aclose()
            raise UpstreamError(502, "Qwen returned a non-streaming response")
        return QwenReplyStream(
            response,
            self._catalog,
            provider_messages,
            on_tool_call,
        )

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

        endpoint = httpx.URL(self._settings.base_url).join("chat/completions")
        upstream_request = self._client.build_request(
            "POST",
            endpoint,
            headers={
                "Authorization": f"Bearer {self._settings.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._settings.model,
                "messages": [
                    *messages,
                    {
                        "role": "tool",
                        "tool_call_id": context.tool_call_id,
                        "content": json.dumps(
                            result.semantic_payload(),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                ],
                "tools": self._catalog.model_definitions(),
                "tool_choice": "none",
                "stream": False,
            },
        )
        try:
            response = await self._client.send(upstream_request)
        except httpx.RequestError as error:
            raise UpstreamError(502, "Unable to reach Qwen") from error
        if not response.is_success:
            body = response.text[:2_000]
            raise UpstreamError(response.status_code, body or response.reason_phrase)
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise UpstreamError(502, "Qwen returned an invalid continuation") from error
        if not isinstance(content, str) or not content.strip():
            raise UpstreamError(502, "Qwen returned an empty continuation")
        return content.strip()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
