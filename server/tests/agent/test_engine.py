"""Agent Loop budgets, recovery, failure handling, and call accounting."""

import copy
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.agent.engine import AgentLoopEngine
from app.agent.errors import AgentLoopBudgetExceeded, AgentLoopRepeatedFailure
from app.agent.journal import InMemoryAgentLoopJournal
from app.agent.model import LoopModelTurn, LoopToolCall
from app.agent.profile import AgentTaskProfile
from app.agent.tools import AgentLoopToolRegistry, LoopToolContext, LoopToolResult
from app.tasks.models import ServerTask, TaskOutcome


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1)


class RecordingTool:
    name = "record"
    description = "Record a value for deterministic Agent Loop tests."
    arguments_model = ToolArguments

    def __init__(self, outcomes: list[LoopToolResult]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []
        self.states_seen: list[dict[str, object]] = []

    async def execute(
        self, arguments: BaseModel, context: LoopToolContext
    ) -> LoopToolResult:
        parsed = ToolArguments.model_validate(arguments)
        self.calls.append(parsed.value)
        self.states_seen.append(copy.deepcopy(context.state))
        return self.outcomes.pop(0)


class ScriptedModel:
    def __init__(self, turns: list[LoopModelTurn]) -> None:
        self.turns = turns
        self.call_count = 0
        self.messages_seen: list[list[dict[str, object]]] = []

    async def next_turn(self, messages, tool_definitions) -> LoopModelTurn:
        self.call_count += 1
        self.messages_seen.append(list(messages))
        return self.turns.pop(0)

    async def close(self) -> None:
        pass


def task() -> ServerTask:
    now = datetime.now(timezone.utc)
    return ServerTask(
        id=uuid4(),
        conversationId=uuid4(),
        capability="test.agent",
        title="Agent regression",
        input={"goal": "verify runtime"},
        status="running",
        phase="starting",
        progress=0,
        requiresConfirmation=False,
        attemptCount=1,
        createdAt=now,
        updatedAt=now,
    )


def tool_turn(*calls: tuple[str, str]) -> LoopModelTurn:
    tool_calls = tuple(
        LoopToolCall(id=call_id, name="record", arguments={"value": value})
        for call_id, value in calls
    )
    return LoopModelTurn(
        content=None,
        tool_calls=tool_calls,
        message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": '{"value":"' + str(call.arguments["value"]) + '"}',
                    },
                }
                for call in tool_calls
            ],
        },
    )


def text_turn(content: str) -> LoopModelTurn:
    return LoopModelTurn(
        content=content,
        tool_calls=(),
        message={"role": "assistant", "content": content},
    )


def profile(
    *,
    max_iterations: int = 6,
    max_tool_calls: int = 20,
    max_identical_failures: int = 3,
) -> AgentTaskProfile:
    return AgentTaskProfile(
        capability="test.agent",
        system_prompt="Use tools and finish deterministically.",
        allowed_tools=("record",),
        steps=("reason", "tools", "finish"),
        max_iterations=max_iterations,
        max_tool_calls=max_tool_calls,
        max_identical_failures=max_identical_failures,
        finalize=lambda _, output: TaskOutcome(summary=str(output["summary"])),
        parse_final_content=lambda content: (
            {"summary": content.removeprefix("FINAL:")}
            if content.startswith("FINAL:")
            else None
        ),
    )


async def no_op_report(*_) -> None:
    pass


async def test_executes_every_tool_in_a_batch_and_counts_calls_exactly(
    anyio_backend,
) -> None:
    model = ScriptedModel([tool_turn(("call-1", "first"), ("call-2", "second"))])
    tool = RecordingTool([
        LoopToolResult({"ok": True}, state_updates={"values": {"first": True}}),
        LoopToolResult(
            {"ok": True},
            state_updates={"values": {"second": True}},
            final_output={"summary": "both tools completed"},
        ),
    ])
    journal = InMemoryAgentLoopJournal()
    current_task = task()
    engine = AgentLoopEngine(model, AgentLoopToolRegistry([tool]), journal)

    outcome = await engine.run(current_task, profile(max_iterations=1), no_op_report)
    events = await journal.load(current_task.id)

    assert outcome.summary == "both tools completed"
    assert model.call_count == 1
    assert tool.calls == ["first", "second"]
    assert tool.states_seen == [{}, {"values": {"first": True}}]
    assert [event.event_type for event in events] == [
        "model.turn", "tool.result", "tool.result"
    ]


async def test_stops_before_executing_a_batch_that_exceeds_tool_budget(
    anyio_backend,
) -> None:
    model = ScriptedModel([tool_turn(("call-1", "first"), ("call-2", "second"))])
    tool = RecordingTool([])
    journal = InMemoryAgentLoopJournal()
    current_task = task()
    engine = AgentLoopEngine(model, AgentLoopToolRegistry([tool]), journal)

    with pytest.raises(AgentLoopBudgetExceeded):
        await engine.run(
            current_task, profile(max_iterations=4, max_tool_calls=1), no_op_report
        )

    assert model.call_count == 1
    assert tool.calls == []
    assert [event.event_type for event in await journal.load(current_task.id)] == [
        "model.turn"
    ]


async def test_iteration_budget_is_exact_and_progress_never_reports_an_extra_round(
    anyio_backend,
) -> None:
    model = ScriptedModel([text_turn("not final") for _ in range(3)])
    journal = InMemoryAgentLoopJournal()
    current_task = task()
    reported_details: list[str] = []

    async def report(_phase, _progress, detail, _steps, _current_step) -> None:
        reported_details.append(detail)

    with pytest.raises(AgentLoopBudgetExceeded):
        await AgentLoopEngine(
            model, AgentLoopToolRegistry([RecordingTool([])]), journal
        ).run(current_task, profile(max_iterations=3), report)

    assert model.call_count == 3
    assert [detail for detail in reported_details if "轮规划" in detail] == [
        "Agent 正在进行第 1/3 轮规划与检查",
        "Agent 正在进行第 2/3 轮规划与检查",
        "Agent 正在进行第 3/3 轮规划与检查",
    ]
    events = await journal.load(current_task.id)
    assert [event.event_type for event in events].count("model.turn") == 3
    assert [event.event_type for event in events].count("loop.feedback") == 3


async def test_persisted_final_output_recovers_without_model_or_tool_reexecution(
    anyio_backend,
) -> None:
    model = ScriptedModel([])
    tool = RecordingTool([])
    journal = InMemoryAgentLoopJournal()
    current_task = task()
    await journal.append(
        current_task.id, "loop.final", {"finalOutput": {"summary": "recovered"}}
    )
    phases: list[str] = []

    async def report(phase, *_args) -> None:
        phases.append(phase)

    outcome = await AgentLoopEngine(
        model, AgentLoopToolRegistry([tool]), journal
    ).run(current_task, profile(), report)

    assert outcome.summary == "recovered"
    assert model.call_count == 0
    assert tool.calls == []
    assert phases == ["finalizing"]


async def test_recovery_executes_only_the_unfinished_call_from_a_persisted_batch(
    anyio_backend,
) -> None:
    current_task = task()
    journal = InMemoryAgentLoopJournal()
    persisted_turn = tool_turn(("done", "first"), ("pending", "second"))
    await journal.append(current_task.id, "model.turn", {"message": persisted_turn.message})
    await journal.append(current_task.id, "tool.result", {
        "toolCallId": "done",
        "toolName": "record",
        "message": {
            "role": "tool", "tool_call_id": "done", "name": "record",
            "content": '{"ok":true}',
        },
        "stateUpdates": {"values": {"first": True}},
    })
    model = ScriptedModel([])
    tool = RecordingTool([
        LoopToolResult(
            {"ok": True}, final_output={"summary": "pending call recovered"}
        )
    ])

    outcome = await AgentLoopEngine(
        model, AgentLoopToolRegistry([tool]), journal
    ).run(current_task, profile(), no_op_report)

    assert outcome.summary == "pending call recovered"
    assert model.call_count == 0
    assert tool.calls == ["second"]
    assert tool.states_seen == [{"values": {"first": True}}]
    events = await journal.load(current_task.id)
    assert [event.event_type for event in events].count("tool.result") == 2


async def test_success_between_equal_failures_resets_repeated_failure_counter(
    anyio_backend,
) -> None:
    failure = LoopToolResult({
        "ok": False,
        "error": {"code": "temporary", "message": "same failure"},
    })
    model = ScriptedModel([
        tool_turn(("call-1", "failure")),
        tool_turn(("call-2", "success")),
        tool_turn(("call-3", "failure")),
        tool_turn(("call-4", "finish")),
    ])
    tool = RecordingTool([
        failure,
        LoopToolResult({"ok": True}),
        failure,
        LoopToolResult({"ok": True}, final_output={"summary": "finished"}),
    ])

    outcome = await AgentLoopEngine(
        model, AgentLoopToolRegistry([tool]), InMemoryAgentLoopJournal()
    ).run(
        task(), profile(max_iterations=4, max_identical_failures=2), no_op_report
    )

    assert outcome.summary == "finished"
    assert model.call_count == 4
    assert len(tool.calls) == 4


async def test_equal_errors_for_different_arguments_are_not_identical_failures(
    anyio_backend,
) -> None:
    failure = LoopToolResult({
        "ok": False,
        "error": {"code": "place_not_verified", "message": "ambiguous place"},
    })
    model = ScriptedModel([
        tool_turn(
            ("call-1", "上海博物馆"),
            ("call-2", "Manner Coffee 上海店"),
            ("call-3", "% Arabica 上海店"),
        ),
        tool_turn(("call-4", "finish")),
    ])
    tool = RecordingTool([
        failure,
        failure,
        failure,
        LoopToolResult({"ok": True}, final_output={"summary": "finished"}),
    ])
    journal = InMemoryAgentLoopJournal()
    current_task = task()

    outcome = await AgentLoopEngine(
        model, AgentLoopToolRegistry([tool]), journal
    ).run(
        current_task,
        profile(max_iterations=2, max_identical_failures=2),
        no_op_report,
    )

    assert outcome.summary == "finished"
    assert tool.calls == [
        "上海博物馆",
        "Manner Coffee 上海店",
        "% Arabica 上海店",
        "finish",
    ]
    tool_events = [
        event for event in await journal.load(current_task.id)
        if event.event_type == "tool.result"
    ]
    assert tool_events[0].payload["arguments"] == {"value": "上海博物馆"}


async def test_identical_failures_stop_at_configured_threshold(
    anyio_backend,
) -> None:
    model = ScriptedModel([
        tool_turn(("call-1", "failure")),
        tool_turn(("call-2", "failure")),
    ])
    failure = LoopToolResult({
        "ok": False,
        "error": {"code": "invalid", "message": "unchanged"},
    })
    tool = RecordingTool([failure, failure])

    with pytest.raises(AgentLoopRepeatedFailure):
        await AgentLoopEngine(
            model, AgentLoopToolRegistry([tool]), InMemoryAgentLoopJournal()
        ).run(
            task(), profile(max_identical_failures=2), no_op_report
        )

    assert model.call_count == 2
    assert len(tool.calls) == 2
