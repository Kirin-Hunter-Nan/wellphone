"""Long-running task runner tests."""

from __future__ import annotations

from app.tasks.jobs import (
    ArtifactDraft,
    InMemoryServerTaskStore,
    ServerTask,
    ServerTaskCreate,
    ServerTaskRunner,
    TaskOutcome,
)


class SuccessfulHandler:
    capability = "test.long-task"

    async def run(self, task: ServerTask, report) -> TaskOutcome:
        steps = ("收集信息", "生成方案", "保存结果")
        await report("researching", 0.25, "正在收集信息", steps, 0)
        await report("composing", 0.75, "正在生成方案", steps, 1)
        return TaskOutcome(
            summary="方案已生成",
            artifacts=(ArtifactDraft(
                kind="json", title="方案", content_type="application/json",
                payload={"destination": task.input["destination"]},
            ),),
        )


async def test_confirmed_server_task_runs_and_persists_artifact(anyio_backend) -> None:
    store = InMemoryServerTaskStore()
    task = await store.create(
        __import__("uuid").uuid4(),
        ServerTaskCreate(
            capability="test.long-task", title="规划旅行",
            input={"destination": "上海"}, requiresConfirmation=True,
        ),
    )
    assert task.status == "waitingForConfirmation"
    assert await ServerTaskRunner(store, [SuccessfulHandler()], worker_id="worker").run_once() is False

    await store.confirm(task.id)
    assert await ServerTaskRunner(store, [SuccessfulHandler()], worker_id="worker").run_once() is True
    completed = await store.get(task.id)

    assert completed is not None
    assert completed.status == "completed"
    assert completed.progress == 1
    assert completed.result_summary == "方案已生成"
    assert completed.artifacts[0].payload == {"destination": "上海"}
    assert [step.status for step in completed.steps] == ["completed"] * 3


class FailingHandler:
    capability = "test.failing"

    async def run(self, task: ServerTask, report) -> TaskOutcome:
        raise RuntimeError("temporary upstream failure")


async def test_runner_retries_then_marks_task_failed(anyio_backend) -> None:
    store = InMemoryServerTaskStore()
    task = await store.create(
        __import__("uuid").uuid4(),
        ServerTaskCreate(
            capability="test.failing", title="失败任务", input={},
            requiresConfirmation=False,
        ),
    )
    runner = ServerTaskRunner(store, [FailingHandler()], worker_id="worker", max_attempts=2)

    await runner.run_once()
    assert (await store.get(task.id)).status == "queued"  # type: ignore[union-attr]
    await runner.run_once()
    failed = await store.get(task.id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.attempt_count == 2


class PermanentFailure(RuntimeError):
    retryable = False


class NonRetryableHandler:
    capability = "test.non-retryable"

    async def run(self, task: ServerTask, report) -> TaskOutcome:
        raise PermanentFailure("invalid repeated model output")


async def test_runner_does_not_retry_permanent_agent_failures(anyio_backend) -> None:
    store = InMemoryServerTaskStore()
    task = await store.create(
        __import__("uuid").uuid4(),
        ServerTaskCreate(
            capability="test.non-retryable", title="不可重试任务", input={},
            requiresConfirmation=False,
        ),
    )

    await ServerTaskRunner(
        store, [NonRetryableHandler()], worker_id="worker", max_attempts=3
    ).run_once()
    failed = await store.get(task.id)

    assert failed is not None
    assert failed.status == "failed"
    assert failed.attempt_count == 1
