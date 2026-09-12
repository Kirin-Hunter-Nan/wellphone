"""Atomic Tool registry validation, safety, and state semantics."""

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.agent.model import LoopToolCall
from app.agent.tools import AgentLoopToolRegistry, LoopToolContext, LoopToolResult
from app.tasks.models import ServerTask


class NestedPayload(BaseModel):
    name: str


class StrictArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(ge=1, le=5)
    payload: NestedPayload


class InspectTool:
    name = "inspect"
    description = "Validate and inspect nested arguments."
    arguments_model = StrictArguments

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.executions = 0

    async def execute(
        self, arguments: BaseModel, context: LoopToolContext
    ) -> LoopToolResult:
        self.executions += 1
        if self.fail:
            raise RuntimeError("adapter unavailable")
        values = StrictArguments.model_validate(arguments)
        return LoopToolResult(
            {"ok": True, "name": values.payload.name},
            state_updates={"facts": {values.payload.name: values.count}},
        )


def context() -> LoopToolContext:
    now = datetime.now(timezone.utc)
    return LoopToolContext(task=ServerTask(
        id=uuid4(), conversationId=uuid4(), capability="test.agent",
        title="Tool test", input={}, status="running", phase="usingTools",
        progress=0.5, requiresConfirmation=False, attemptCount=1,
        createdAt=now, updatedAt=now,
    ))


async def test_definitions_expose_only_allowed_tools_with_json_schema(
    anyio_backend,
) -> None:
    registry = AgentLoopToolRegistry([InspectTool()])

    definitions = registry.definitions(("inspect",))

    assert len(definitions) == 1
    function = definitions[0]["function"]
    assert function["name"] == "inspect"
    assert function["parameters"]["additionalProperties"] is False
    assert set(function["parameters"]["required"]) == {"count", "payload"}


async def test_disallowed_and_missing_tools_never_execute(anyio_backend) -> None:
    tool = InspectTool()
    registry = AgentLoopToolRegistry([tool])
    active_context = context()

    disallowed = await registry.execute(
        LoopToolCall("call-1", "inspect", {"count": 1, "payload": {"name": "x"}}),
        active_context,
        (),
    )
    missing = await registry.execute(
        LoopToolCall("call-2", "missing", {}), active_context, ("missing",)
    )

    assert disallowed.observation["error"]["code"] == "tool_not_allowed"
    assert missing.observation["error"]["code"] == "tool_not_found"
    assert tool.executions == 0


async def test_invalid_arguments_return_observation_without_executing_tool(
    anyio_backend,
) -> None:
    tool = InspectTool()
    result = await AgentLoopToolRegistry([tool]).execute(
        LoopToolCall("call-1", "inspect", {"count": 99, "payload": {"name": "x"}}),
        context(),
        ("inspect",),
    )

    assert result.observation["ok"] is False
    assert result.observation["error"]["code"] == "invalid_tool_arguments"
    assert tool.executions == 0


async def test_malformed_json_arguments_return_a_corrective_observation(
    anyio_backend,
) -> None:
    tool = InspectTool()
    result = await AgentLoopToolRegistry([tool]).execute(
        LoopToolCall(
            "call-1",
            "inspect",
            {},
            argument_error="Tool arguments were not valid JSON at line 1, column 40",
        ),
        context(),
        ("inspect",),
    )

    assert result.observation["ok"] is False
    assert result.observation["error"] == {
        "code": "invalid_tool_arguments",
        "message": (
            "Tool arguments were not valid JSON at line 1, column 40. "
            "Return exactly one valid JSON object that matches the Tool schema."
        ),
    }
    assert tool.executions == 0


async def test_repairs_nested_json_arguments_and_merges_tool_state(
    anyio_backend,
) -> None:
    tool = InspectTool()
    active_context = context()
    active_context.state = {"facts": {"existing": 2}, "stable": True}

    result = await AgentLoopToolRegistry([tool]).execute(
        LoopToolCall(
            "call-1", "inspect", {"count": 3, "payload": '{"name":"new"}'},
        ),
        active_context,
        ("inspect",),
    )

    assert result.observation == {"ok": True, "name": "new"}
    assert active_context.state == {
        "facts": {"existing": 2, "new": 3},
        "stable": True,
    }
    assert tool.executions == 1


async def test_tool_exception_becomes_retryable_observation_for_the_loop(
    anyio_backend,
) -> None:
    tool = InspectTool(fail=True)

    result = await AgentLoopToolRegistry([tool]).execute(
        LoopToolCall("call-1", "inspect", {"count": 1, "payload": {"name": "x"}}),
        context(),
        ("inspect",),
    )

    assert result.observation == {
        "ok": False,
        "error": {
            "code": "tool_execution_failed",
            "message": "adapter unavailable",
        },
    }
    assert tool.executions == 1
