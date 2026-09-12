"""Model boundary used by the Agent Loop."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json
from typing import Protocol

import httpx

from app.agent.errors import AgentLoopError
from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class LoopToolCall:
    id: str
    name: str
    arguments: dict[str, object]
    argument_error: str | None = None


@dataclass(frozen=True, slots=True)
class LoopModelTurn:
    content: str | None
    tool_calls: tuple[LoopToolCall, ...]
    message: dict[str, object]


class AgentLoopModel(Protocol):
    async def next_turn(
        self,
        messages: Sequence[dict[str, object]],
        tool_definitions: Sequence[dict[str, object]],
    ) -> LoopModelTurn: ...

    async def close(self) -> None: ...


class QwenAgentLoopModel:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=120)
        self._owns_client = client is None

    async def next_turn(
        self,
        messages: Sequence[dict[str, object]],
        tool_definitions: Sequence[dict[str, object]],
    ) -> LoopModelTurn:
        endpoint = httpx.URL(self._settings.base_url).join("chat/completions")
        response = await self._client.post(
            endpoint,
            headers={"Authorization": f"Bearer {self._settings.api_key}"},
            json={
                "model": self._settings.model,
                "messages": list(messages),
                "tools": list(tool_definitions),
                "tool_choice": "auto",
                "stream": False,
            },
        )
        response.raise_for_status()
        raw_message = response.json()["choices"][0]["message"]
        calls: list[LoopToolCall] = []
        normalized_calls: list[dict[str, object]] = []
        for raw_call in raw_message.get("tool_calls") or []:
            function = raw_call.get("function") or {}
            arguments_value = function.get("arguments") or "{}"
            argument_error: str | None = None
            if isinstance(arguments_value, str):
                try:
                    arguments = json.loads(arguments_value)
                except json.JSONDecodeError as error:
                    arguments = {}
                    argument_error = (
                        "Tool arguments were not valid JSON "
                        f"at line {error.lineno}, column {error.colno}"
                    )
            else:
                arguments = arguments_value
            if not isinstance(arguments, dict):
                arguments = {}
                argument_error = "Tool arguments must be a JSON object"
            call = LoopToolCall(
                id=str(raw_call.get("id") or ""),
                name=str(function.get("name") or ""),
                arguments=arguments,
                argument_error=argument_error,
            )
            if not call.id or not call.name:
                raise AgentLoopError("Model returned an invalid Tool call")
            calls.append(call)
            normalized_calls.append({
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(
                        call.arguments, ensure_ascii=False, separators=(",", ":")
                    ),
                },
            })
        content = raw_message.get("content")
        message: dict[str, object] = {
            "role": "assistant",
            "content": content if isinstance(content, str) else None,
        }
        if normalized_calls:
            message["tool_calls"] = normalized_calls
        return LoopModelTurn(
            content=content if isinstance(content, str) else None,
            tool_calls=tuple(calls),
            message=message,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
