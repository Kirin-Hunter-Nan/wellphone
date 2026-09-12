"""Server-task adapter for capability-specific Agent Loop profiles."""

from __future__ import annotations

from collections.abc import Sequence

from app.agent.engine import AgentLoopEngine
from app.agent.errors import AgentLoopError
from app.agent.profile import AgentTaskProfile
from app.tasks.jobs import ProgressReporter, ServerTask, TaskOutcome

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
