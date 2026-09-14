"""LangGraph-backed Agent Loop orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.errors import AgentLoopBudgetExceeded, AgentLoopRepeatedFailure
from app.agent.journal import AgentLoopJournal
from app.agent.model import AgentLoopModel, LoopToolCall
from app.agent.profile import AgentTaskProfile
from app.agent.tools import AgentLoopToolRegistry, LoopToolContext, _merge_state
from app.tasks.models import ServerTask, TaskOutcome
from app.tasks.runner import ProgressReporter

class AgentGraphState(TypedDict):
    messages: list[dict[str, object]]
    tool_state: dict[str, object]
    pending_calls: tuple[LoopToolCall, ...]
    model_turn_count: int
    tool_call_count: int
    failure_signature: str | None
    identical_failure_count: int
    failure_counts: dict[str, int]
    final_output: dict[str, object] | None
    stop_reason: Literal["budget", "repeated_failure"] | None


@dataclass(frozen=True, slots=True)
class ToolBatchOutcome:
    messages: list[dict[str, object]]
    tool_state: dict[str, object]
    executed_count: int
    failure_signature: str | None
    identical_failure_count: int
    failure_counts: dict[str, int]
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
                failure_counts=current["failure_counts"],
                report=report,
            )
            return {
                "messages": batch.messages,
                "tool_state": batch.tool_state,
                "pending_calls": (),
                "tool_call_count": current["tool_call_count"] + batch.executed_count,
                "failure_signature": batch.failure_signature,
                "identical_failure_count": batch.identical_failure_count,
                "failure_counts": batch.failure_counts,
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
                "Agent 反复收到相同的 Tool 错误，任务已提前停止"
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
        failure_counts: dict[str, int] = {}
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
                arguments = event.payload.get("arguments")
                current_signature = _failure_signature(
                    str(event.payload.get("toolName") or ""),
                    arguments if isinstance(arguments, dict) else {},
                    observation,
                )
                if current_signature is None:
                    failure_signature, identical_failure_count = None, 0
                    failure_counts.clear()
                else:
                    failure_signature = current_signature
                    identical_failure_count = failure_counts.get(current_signature, 0) + 1
                    failure_counts[current_signature] = identical_failure_count
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
            failure_counts=failure_counts,
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
        failure_counts: dict[str, int],
        report: ProgressReporter,
    ) -> ToolBatchOutcome:
        executed_count = 0
        failure_counts = dict(failure_counts)
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
            context.tool_call_id = call.id
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
                "arguments": call.arguments,
                "message": message,
                "stateUpdates": result.state_updates,
            }
            if result.final_output is not None:
                payload["finalOutput"] = result.final_output
            await self._journal.append(task.id, "tool.result", payload)

            current_signature = _failure_signature(
                call.name, call.arguments, result.observation
            )
            if current_signature is None:
                failure_signature, identical_failure_count = None, 0
                failure_counts.clear()
            else:
                failure_signature = current_signature
                identical_failure_count = failure_counts.get(current_signature, 0) + 1
                failure_counts[current_signature] = identical_failure_count

            if result.final_output is not None:
                return ToolBatchOutcome(
                    messages, context.state, executed_count,
                    failure_signature, identical_failure_count,
                    failure_counts,
                    final_output=result.final_output,
                )
            if identical_failure_count >= profile.max_identical_failures:
                return ToolBatchOutcome(
                    messages, context.state, executed_count,
                    failure_signature, identical_failure_count,
                    failure_counts,
                    repeated_failure=True,
                )
        return ToolBatchOutcome(
            messages, context.state, executed_count,
            failure_signature, identical_failure_count,
            failure_counts,
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
    tool_name: str,
    arguments: dict[str, object],
    observation: dict[str, object],
) -> str | None:
    if observation.get("ok") is not False:
        return None
    error = observation.get("error")
    if isinstance(error, dict):
        detail = {
            "code": error.get("code"),
            "message": error.get("message"),
        }
        signature_arguments: dict[str, object] = arguments
    else:
        issues = observation.get("issues")
        detail = {
            "status": observation.get("status"),
            "issues": _normalized_validation_issues(issues),
        }
        # A validation retry changes the submitted plan arguments by design.
        # Compare its deterministic issue list instead, otherwise a model can
        # repeat the same failed strategy until the full iteration budget is
        # exhausted merely by rewording plan items.
        signature_arguments = {} if isinstance(issues, list) else arguments
    return tool_name + ":" + json.dumps(
        {"arguments": signature_arguments, "failure": detail},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _normalized_validation_issues(issues: object) -> object:
    if not isinstance(issues, list):
        return issues
    normalized: list[object] = []
    calendar_marker = " overlaps existing calendar event "
    for issue in issues:
        if isinstance(issue, str) and calendar_marker in issue:
            # The model often renames a flexible item while leaving it on top
            # of the same calendar blocker. That is the same failed strategy.
            normalized.append(
                "calendar_overlap:" + issue.split(calendar_marker, 1)[1]
            )
        else:
            normalized.append(issue)
    return sorted(normalized, key=str)
