"""Decode Qwen's streaming chunks into the WellPhone SSE protocol."""

from dataclasses import dataclass
import json
from typing import AsyncIterator
from uuid import uuid4

import httpx

from app.api.protocol import (
    assistant_delta,
    encode_sse,
    response_completed,
    response_failed,
    response_started,
    tool_requested,
)
from app.providers.base import ProviderToolCallContext, ToolCallContextSink
from app.tools.catalog import ModelToolCatalog, ToolCatalogError


@dataclass(slots=True)
class ToolCallAccumulator:
    tool_call_id: str = ""
    name: str = ""
    arguments: str = ""

    def append(self, chunk: dict[str, object]) -> None:
        tool_call_id = chunk.get("id")
        if isinstance(tool_call_id, str) and tool_call_id:
            self.tool_call_id = tool_call_id
        function = chunk.get("function") or {}
        if not isinstance(function, dict):
            return
        name = function.get("name")
        if isinstance(name, str) and name:
            self.name = name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            self.arguments += arguments


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
        tool_calls: dict[int, ToolCallAccumulator] = {}
        assistant_text = ""
        try:
            async for line in self._response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                if payload == "[DONE]":
                    async for event in self._completion_events(tool_calls, assistant_text):
                        yield event
                    return
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    yield self._failed(
                        "invalid_upstream_event", "模型返回了无效的流事件。"
                    )
                    return
                if not isinstance(chunk, dict):
                    yield self._failed(
                        "invalid_upstream_event", "模型返回了无效的流事件。"
                    )
                    return
                choices = chunk.get("choices", [])
                if not isinstance(choices, list):
                    yield self._failed(
                        "invalid_upstream_event", "模型返回了无效的流事件。"
                    )
                    return
                for choice in choices:
                    if not isinstance(choice, dict):
                        yield self._failed(
                            "invalid_upstream_event", "模型返回了无效的流事件。"
                        )
                        return
                    delta = choice.get("delta") or {}
                    if not isinstance(delta, dict):
                        yield self._failed(
                            "invalid_upstream_event", "模型返回了无效的流事件。"
                        )
                        return
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        assistant_text += text
                        yield encode_sse(assistant_delta(self._response_id, text))
                    raw_tool_calls = delta.get("tool_calls") or []
                    if not isinstance(raw_tool_calls, list):
                        yield self._failed(
                            "invalid_upstream_event", "模型返回了无效的流事件。"
                        )
                        return
                    for tool_call in raw_tool_calls:
                        if not isinstance(tool_call, dict):
                            yield self._failed(
                                "invalid_upstream_event", "模型返回了无效的流事件。"
                            )
                            return
                        index = tool_call.get("index", 0)
                        if isinstance(index, int):
                            tool_calls.setdefault(index, ToolCallAccumulator()).append(tool_call)
            yield self._failed(
                "upstream_stream_interrupted", "模型响应流意外中断。"
            )
        finally:
            await self._response.aclose()

    async def _completion_events(
        self,
        tool_calls: dict[int, ToolCallAccumulator],
        assistant_text: str,
    ) -> AsyncIterator[str]:
        completed_calls = [
            tool_calls[index]
            for index in sorted(tool_calls)
            if tool_calls[index].name
        ]
        if len(completed_calls) > 1:
            yield self._failed(
                "parallel_tool_calls_unsupported",
                "当前设备运行时一次只支持一个工具请求。",
            )
            return
        try:
            for accumulated in completed_calls:
                normalized = self._catalog.normalize(
                    model_name=accumulated.name,
                    tool_call_id=accumulated.tool_call_id or f"call_{uuid4().hex}",
                    arguments_json=accumulated.arguments,
                )
                await self._save_tool_context(normalized, accumulated, assistant_text)
                yield encode_sse(tool_requested(self._response_id, normalized))
        except ToolCatalogError:
            yield self._failed(
                "invalid_tool_request", "模型返回了无法执行的工具请求。"
            )
            return
        except Exception:
            yield self._failed(
                "tool_context_persistence_failed", "工具请求暂时无法保存，请稍后重试。"
            )
            return
        yield encode_sse(response_completed(self._response_id))

    async def _save_tool_context(self, normalized, accumulated, assistant_text: str) -> None:
        if self._on_tool_call is None:
            return
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

    def _failed(self, code: str, message: str) -> str:
        return encode_sse(response_failed(self._response_id, code, message))
