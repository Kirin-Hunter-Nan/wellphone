"""Tool contracts, context, and registry for Agent Loop execution."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import json
from typing import Protocol

from pydantic import BaseModel, ValidationError

from app.agent.errors import AgentLoopError
from app.agent.model import LoopToolCall
from app.tasks.jobs import ServerTask


@dataclass(slots=True)
class LoopToolContext:
    task: ServerTask
    state: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LoopToolResult:
    observation: dict[str, object]
    state_updates: dict[str, object] = field(default_factory=dict)
    final_output: dict[str, object] | None = None


class AgentLoopTool(Protocol):
    name: str
    description: str
    arguments_model: type[BaseModel]

    async def execute(
        self, arguments: BaseModel, context: LoopToolContext
    ) -> LoopToolResult: ...


class AgentLoopToolRegistry:
    def __init__(self, tools: Sequence[AgentLoopTool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def definitions(self, allowed: Sequence[str]) -> list[dict[str, object]]:
        definitions: list[dict[str, object]] = []
        for name in allowed:
            tool = self._tools.get(name)
            if tool is None:
                raise AgentLoopError(f"Task profile references unknown Tool: {name}")
            definitions.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.arguments_model.model_json_schema(by_alias=True),
                },
            })
        return definitions

    async def execute(
        self,
        call: LoopToolCall,
        context: LoopToolContext,
        allowed: Sequence[str],
    ) -> LoopToolResult:
        if call.name not in allowed:
            return LoopToolResult({
                "ok": False,
                "error": {"code": "tool_not_allowed", "message": f"Tool {call.name} is not allowed"},
            })
        tool = self._tools.get(call.name)
        if tool is None:
            return LoopToolResult({
                "ok": False,
                "error": {"code": "tool_not_found", "message": f"Tool {call.name} does not exist"},
            })
        try:
            arguments = tool.arguments_model.model_validate(call.arguments)
        except ValidationError as original_error:
            repaired = _repair_nested_json_arguments(call.arguments)
            try:
                arguments = tool.arguments_model.model_validate(repaired)
            except ValidationError as repaired_error:
                error = repaired_error if repaired != call.arguments else original_error
                return LoopToolResult({
                    "ok": False,
                    "error": {
                        "code": "invalid_tool_arguments",
                        "message": str(error.errors(include_url=False)[0].get("msg")),
                    },
                })
        try:
            result = await tool.execute(arguments, context)
        except Exception as error:
            return LoopToolResult({
                "ok": False,
                "error": {"code": "tool_execution_failed", "message": str(error)},
            })
        _merge_state(context.state, result.state_updates)
        return result

def _merge_state(target: dict[str, object], updates: dict[str, object]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            target[key].update(value)  # type: ignore[union-attr]
        else:
            target[key] = value

def _repair_nested_json_arguments(
    arguments: dict[str, object],
) -> dict[str, object]:
    repaired = dict(arguments)
    for key, value in arguments.items():
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if not candidate.startswith(("{", "[")):
            continue
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, (dict, list)):
            repaired[key] = decoded
    return repaired
