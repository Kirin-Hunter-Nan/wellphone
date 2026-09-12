"""Background task-worker composition root."""

from __future__ import annotations

import asyncio
import os
import socket

from app.core.config import load_settings
from app.agent.engine import AgentLoopEngine
from app.agent.handler import AgentLoopTaskHandler
from app.agent.journal import PostgreSQLAgentLoopJournal
from app.agent.model import QwenAgentLoopModel
from app.agent.tools import AgentLoopToolRegistry
from app.tasks.models import ArtifactDraft, ServerTask, TaskOutcome
from app.tasks.runner import ServerTaskRunner
from app.tasks.stores.postgres import PostgreSQLServerTaskStore
from app.tools.travel.maps import AppleMapsSearchClient
from app.tools.travel.profile import make_travel_profile
from app.tools.travel.tools import ItinerarySubmitTool, PlacesSearchTool


class HarnessProbeHandler:
    """Small deterministic job used to verify the long-task pipeline end to end."""

    capability = "system.long-task-probe"

    async def run(self, task: ServerTask, report) -> TaskOutcome:
        steps = ("读取任务输入", "执行后台工作", "保存任务产物")
        await report("understanding", 0.15, "正在读取任务输入", steps, 0)
        await asyncio.sleep(0.2)
        await report("executing", 0.55, "后台工作正在进行", steps, 1)
        await asyncio.sleep(0.2)
        await report("saving", 0.9, "正在保存结果", steps, 2)
        return TaskOutcome(
            summary="服务端长任务 Harness 验证完成。",
            artifacts=(ArtifactDraft(
                kind="json",
                title="Harness 验证结果",
                content_type="application/json",
                payload={"echo": task.input},
            ),),
        )


async def run_worker() -> None:
    settings = load_settings()
    store = PostgreSQLServerTaskStore(settings.database_url)
    journal = PostgreSQLAgentLoopJournal(settings.database_url)
    model = QwenAgentLoopModel(settings)
    maps = AppleMapsSearchClient(settings.apple_maps_token)
    tools = AgentLoopToolRegistry([
        PlacesSearchTool(maps),
        ItinerarySubmitTool(),
    ])
    loop_handler = AgentLoopTaskHandler(
        AgentLoopEngine(model, tools, journal),
        [make_travel_profile()],
    )
    await store.initialize()
    await journal.initialize()
    runner = ServerTaskRunner(
        store,
        [HarnessProbeHandler(), loop_handler],
        worker_id=f"{socket.gethostname()}:{os.getpid()}",
        lease_seconds=settings.task_worker_lease_seconds,
        max_attempts=settings.task_worker_max_attempts,
    )
    try:
        while True:
            handled = await runner.run_once()
            if not handled:
                await asyncio.sleep(settings.task_worker_poll_seconds)
    finally:
        await maps.close()
        await model.close()
        await journal.close()
        await store.close()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
