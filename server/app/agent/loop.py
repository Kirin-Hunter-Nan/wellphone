"""LangGraph-backed agent execution loop and durable journal."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Literal, Protocol, TypedDict
from uuid import UUID

import httpx
from langgraph.graph import END, START, StateGraph
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.tasks.jobs import ProgressReporter, ServerTask, TaskOutcome


class AgentLoopError(RuntimeError):
    retryable = False

    pass


class AgentLoopBudgetExceeded(AgentLoopError):
    pass


class AgentLoopRepeatedFailure(AgentLoopError):
    pass


@dataclass(frozen=True, slots=True)
class LoopToolCall:
    id: str
    name: str
    arguments: dict[str, object]


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
            arguments = (
                json.loads(arguments_value)
                if isinstance(arguments_value, str)
                else arguments_value
            )
            if not isinstance(arguments, dict):
                raise AgentLoopError("Model Tool arguments must be a JSON object")
            call = LoopToolCall(
                id=str(raw_call.get("id") or ""),
                name=str(function.get("name") or ""),
                arguments=arguments,
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


@dataclass(frozen=True, slots=True)
class AgentTaskProfile:
    capability: str
    system_prompt: str
    allowed_tools: tuple[str, ...]
    steps: tuple[str, ...]
    finalize: Callable[[ServerTask, dict[str, object]], TaskOutcome]
    parse_final_content: Callable[[str], dict[str, object] | None]
    max_iterations: int = 6
    max_tool_calls: int = 20
    max_identical_failures: int = 3

    def initial_messages(self, task: ServerTask) -> list[dict[str, object]]:
        return [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "user",
                "content": (
                    "完成下面的任务目标。根据需要自主调用可用工具；只有在信息经过核对且满足约束后才能提交最终结果。\n"
                    + json.dumps(task.input, ensure_ascii=False, separators=(",", ":"))
                ),
            },
        ]


@dataclass(frozen=True, slots=True)
class LoopJournalEvent:
    sequence: int
    event_type: str
    payload: dict[str, object]


class AgentLoopJournal(Protocol):
    async def initialize(self) -> None: ...
    async def load(self, task_id: UUID) -> list[LoopJournalEvent]: ...
    async def append(
        self, task_id: UUID, event_type: str, payload: dict[str, object]
    ) -> LoopJournalEvent: ...
    async def close(self) -> None: ...


class InMemoryAgentLoopJournal:
    def __init__(self) -> None:
        self._events: dict[UUID, list[LoopJournalEvent]] = {}

    async def initialize(self) -> None:
        pass

    async def load(self, task_id: UUID) -> list[LoopJournalEvent]:
        return list(self._events.get(task_id, []))

    async def append(
        self, task_id: UUID, event_type: str, payload: dict[str, object]
    ) -> LoopJournalEvent:
        events = self._events.setdefault(task_id, [])
        event = LoopJournalEvent(len(events) + 1, event_type, payload)
        events.append(event)
        return event

    async def close(self) -> None:
        pass


class PostgreSQLAgentLoopJournal:
    def __init__(self, database_url: str) -> None:
        self._pool = AsyncConnectionPool(
            conninfo=database_url, min_size=1, max_size=3, open=False,
            check=AsyncConnectionPool.check_connection,
        )

    async def initialize(self) -> None:
        await self._pool.open(wait=True, timeout=30)
        async with self._pool.connection() as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS agent_loop_events (
                    task_id UUID NOT NULL REFERENCES server_tasks(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (task_id, sequence)
                )
            """)

    async def load(self, task_id: UUID) -> list[LoopJournalEvent]:
        async with self._pool.connection() as connection:
            rows = await (await connection.execute(
                "SELECT sequence,event_type,payload_json FROM agent_loop_events "
                "WHERE task_id=%s ORDER BY sequence",
                (task_id,),
            )).fetchall()
        return [LoopJournalEvent(row[0], row[1], row[2]) for row in rows]

    async def append(
        self, task_id: UUID, event_type: str, payload: dict[str, object]
    ) -> LoopJournalEvent:
        now = datetime.now(timezone.utc)
        async with self._pool.connection() as connection:
            cursor = await connection.execute("""
                INSERT INTO agent_loop_events (task_id,sequence,event_type,payload_json,created_at)
                SELECT %s,COALESCE(MAX(sequence),0)+1,%s,%s::jsonb,%s
                FROM agent_loop_events WHERE task_id=%s
                RETURNING sequence
            """, (
                task_id, event_type,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now, task_id,
            ))
            sequence = (await cursor.fetchone())[0]
        return LoopJournalEvent(sequence, event_type, payload)

    async def close(self) -> None:
        await self._pool.close()


class AgentGraphState(TypedDict):
    messages: list[dict[str, object]]
    tool_state: dict[str, object]
    pending_calls: tuple[LoopToolCall, ...]
    model_turn_count: int
    tool_call_count: int
    failure_signature: str | None
    identical_failure_count: int
    final_output: dict[str, object] | None
    stop_reason: Literal["budget", "repeated_failure"] | None


@dataclass(frozen=True, slots=True)
class ToolBatchOutcome:
    messages: list[dict[str, object]]
    tool_state: dict[str, object]
    executed_count: int
    failure_signature: str | None
    identical_failure_count: int
    final_output: dict[str, object] | None = None
    repeated_failure: bool = False


class AgentLoopEngine:
    def __init__(
        self,
        model: AgentLoopModel,
        tools: AgentLoopToolRegistry,
        journal: AgentLoopJournal,
    ) -> None:
        self._model = model
        self._tools = tools
        self._journal = journal

    async def run(
        self,
        task: ServerTask,
        profile: AgentTaskProfile,
        report: ProgressReporter,
    ) -> TaskOutcome:
        state = await self._restore_state(task, profile)
        if state["final_output"] is not None:
            return await self._finalize(task, profile, state["final_output"], report)

        definitions = self._tools.definitions(profile.allowed_tools)

        async def reason_node(current: AgentGraphState) -> dict[str, object]:
            if current["model_turn_count"] >= profile.max_iterations:
                return {"stop_reason": "budget"}

            turn_number = current["model_turn_count"] + 1
            progress = min(
                0.12 + (turn_number / profile.max_iterations) * 0.60,
                0.72,
            )
            await report(
                "reasoning",
                progress,
                f"Agent 正在进行第 {turn_number}/{profile.max_iterations} 轮规划与检查",
                profile.steps,
                min(turn_number - 1, max(len(profile.steps) - 2, 0)),
            )
            turn = await self._model.next_turn(current["messages"], definitions)
            messages = [*current["messages"], turn.message]
            await self._journal.append(
                task.id, "model.turn", {"message": turn.message}
            )
            update: dict[str, object] = {
                "messages": messages,
                "model_turn_count": turn_number,
                "pending_calls": turn.tool_calls,
            }
            if turn.tool_calls:
                if current["tool_call_count"] + len(turn.tool_calls) > profile.max_tool_calls:
                    update["stop_reason"] = "budget"
                return update

            if turn.content:
                final = profile.parse_final_content(turn.content)
                if final is not None:
                    await self._journal.append(
                        task.id, "loop.final", {"finalOutput": final}
                    )
                    update["final_output"] = final
                    return update

            correction = {
                "role": "user",
                "content": "任务尚未完成。请调用工具核对信息，并通过提交工具返回符合 Schema 的最终结果。",
            }
            messages.append(correction)
            await self._journal.append(
                task.id, "loop.feedback", {"message": correction}
            )
            update["messages"] = messages
            return update

        async def tools_node(current: AgentGraphState) -> dict[str, object]:
            if (
                current["tool_call_count"] + len(current["pending_calls"])
                > profile.max_tool_calls
            ):
                return {"stop_reason": "budget"}
            context = LoopToolContext(task=task, state=dict(current["tool_state"]))
            batch = await self._execute_calls(
                task=task,
                profile=profile,
                calls=current["pending_calls"],
                messages=list(current["messages"]),
                context=context,
                completed_tool_calls=current["tool_call_count"],
                failure_signature=current["failure_signature"],
                identical_failure_count=current["identical_failure_count"],
                report=report,
            )
            return {
                "messages": batch.messages,
                "tool_state": batch.tool_state,
                "pending_calls": (),
                "tool_call_count": current["tool_call_count"] + batch.executed_count,
                "failure_signature": batch.failure_signature,
                "identical_failure_count": batch.identical_failure_count,
                "final_output": batch.final_output,
                "stop_reason": "repeated_failure" if batch.repeated_failure else None,
            }

        def entry_route(current: AgentGraphState) -> str:
            return "tools" if current["pending_calls"] else "reason"

        def next_route(current: AgentGraphState) -> str:
            if current["final_output"] is not None or current["stop_reason"] is not None:
                return END
            return "tools" if current["pending_calls"] else "reason"

        builder = StateGraph(AgentGraphState)
        builder.add_node("reason", reason_node)
        builder.add_node("tools", tools_node)
        builder.add_conditional_edges(START, entry_route)
        builder.add_conditional_edges("reason", next_route)
        builder.add_conditional_edges("tools", next_route)
        graph = builder.compile(name=f"wellphone-{profile.capability}")
        result = await graph.ainvoke(
            state,
            config={"recursion_limit": profile.max_iterations * 2 + 4},
        )

        final_output = result.get("final_output")
        if isinstance(final_output, dict):
            return await self._finalize(task, profile, final_output, report)
        if result.get("stop_reason") == "repeated_failure":
            raise AgentLoopRepeatedFailure(
                "Agent 连续收到相同的 Tool 错误，任务已提前停止"
            )
        raise AgentLoopBudgetExceeded(
            f"Agent 达到最多 {profile.max_iterations} 轮，仍未生成有效结果"
        )

    async def _restore_state(
        self, task: ServerTask, profile: AgentTaskProfile
    ) -> AgentGraphState:
        messages = profile.initial_messages(task)
        tool_state: dict[str, object] = {}
        model_turn_count = 0
        tool_call_count = 0
        failure_signature: str | None = None
        identical_failure_count = 0
        final_output: dict[str, object] | None = None
        for event in await self._journal.load(task.id):
            message = event.payload.get("message")
            if isinstance(message, dict):
                messages.append(message)
            updates = event.payload.get("stateUpdates")
            if isinstance(updates, dict):
                _merge_state(tool_state, updates)
            if event.event_type == "model.turn":
                model_turn_count += 1
            if event.event_type == "tool.result":
                tool_call_count += 1
                observation = _message_observation(message)
                current_signature = _failure_signature(
                    str(event.payload.get("toolName") or ""), observation
                )
                if current_signature is None:
                    failure_signature, identical_failure_count = None, 0
                elif current_signature == failure_signature:
                    identical_failure_count += 1
                else:
                    failure_signature, identical_failure_count = current_signature, 1
            persisted_final = event.payload.get("finalOutput")
            if isinstance(persisted_final, dict):
                final_output = persisted_final
        return AgentGraphState(
            messages=messages,
            tool_state=tool_state,
            pending_calls=_pending_tool_calls(messages),
            model_turn_count=model_turn_count,
            tool_call_count=tool_call_count,
            failure_signature=failure_signature,
            identical_failure_count=identical_failure_count,
            final_output=final_output,
            stop_reason=None,
        )

    async def _execute_calls(
        self,
        *,
        task: ServerTask,
        profile: AgentTaskProfile,
        calls: Sequence[LoopToolCall],
        messages: list[dict[str, object]],
        context: LoopToolContext,
        completed_tool_calls: int,
        failure_signature: str | None,
        identical_failure_count: int,
        report: ProgressReporter,
    ) -> ToolBatchOutcome:
        executed_count = 0
        for offset, call in enumerate(calls):
            if completed_tool_calls + offset >= profile.max_tool_calls:
                break
            await report(
                "usingTools",
                min(0.25 + (completed_tool_calls + offset) * 0.025, 0.82),
                f"正在调用工具：{call.name}",
                profile.steps,
                min(1, len(profile.steps) - 1),
            )
            result = await self._tools.execute(call, context, profile.allowed_tools)
            executed_count += 1
            content = json.dumps(
                result.observation, ensure_ascii=False, separators=(",", ":")
            )
            message: dict[str, object] = {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": content,
            }
            messages.append(message)
            payload: dict[str, object] = {
                "toolCallId": call.id,
                "toolName": call.name,
                "message": message,
                "stateUpdates": result.state_updates,
            }
            if result.final_output is not None:
                payload["finalOutput"] = result.final_output
            await self._journal.append(task.id, "tool.result", payload)

            current_signature = _failure_signature(call.name, result.observation)
            if current_signature is None:
                failure_signature, identical_failure_count = None, 0
            elif current_signature == failure_signature:
                identical_failure_count += 1
            else:
                failure_signature, identical_failure_count = current_signature, 1

            if result.final_output is not None:
                return ToolBatchOutcome(
                    messages, context.state, executed_count,
                    failure_signature, identical_failure_count,
                    final_output=result.final_output,
                )
            if identical_failure_count >= profile.max_identical_failures:
                return ToolBatchOutcome(
                    messages, context.state, executed_count,
                    failure_signature, identical_failure_count,
                    repeated_failure=True,
                )
        return ToolBatchOutcome(
            messages, context.state, executed_count,
            failure_signature, identical_failure_count,
        )

    @staticmethod
    async def _finalize(
        task: ServerTask,
        profile: AgentTaskProfile,
        final_output: dict[str, object],
        report: ProgressReporter,
    ) -> TaskOutcome:
        await report(
            "finalizing", 0.92, "正在生成最终任务产物",
            profile.steps, len(profile.steps) - 1,
        )
        return profile.finalize(task, final_output)


class AgentLoopTaskHandler:
    def __init__(
        self,
        engine: AgentLoopEngine,
        profiles: Sequence[AgentTaskProfile],
    ) -> None:
        self._engine = engine
        self._profiles = {profile.capability: profile for profile in profiles}
        self.capabilities = frozenset(self._profiles)

    async def run(self, task: ServerTask, report: ProgressReporter) -> TaskOutcome:
        profile = self._profiles.get(task.capability)
        if profile is None:
            raise AgentLoopError(f"No Agent task profile for {task.capability}")
        return await self._engine.run(task, profile, report)


def _pending_tool_calls(messages: Sequence[dict[str, object]]) -> tuple[LoopToolCall, ...]:
    completed_ids = {
        str(message.get("tool_call_id"))
        for message in messages
        if message.get("role") == "tool"
    }
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        pending: list[LoopToolCall] = []
        for raw_call in message.get("tool_calls") or []:
            if not isinstance(raw_call, dict) or str(raw_call.get("id")) in completed_ids:
                continue
            function = raw_call.get("function") or {}
            raw_arguments = function.get("arguments") or "{}"
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            if isinstance(arguments, dict):
                pending.append(LoopToolCall(
                    id=str(raw_call.get("id")),
                    name=str(function.get("name")),
                    arguments=arguments,
                ))
        return tuple(pending)
    return ()


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


def _message_observation(message: object) -> dict[str, object]:
    if not isinstance(message, dict):
        return {}
    content = message.get("content")
    if not isinstance(content, str):
        return {}
    try:
        observation = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return observation if isinstance(observation, dict) else {}


def _failure_signature(
    tool_name: str, observation: dict[str, object]
) -> str | None:
    if observation.get("ok") is not False:
        return None
    error = observation.get("error")
    if isinstance(error, dict):
        detail = {
            "code": error.get("code"),
            "message": error.get("message"),
        }
    else:
        detail = {
            "status": observation.get("status"),
            "issues": observation.get("issues"),
        }
    return tool_name + ":" + json.dumps(
        detail, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
