"""Task-step projection shared by persistence implementations."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

from app.tasks.models import ServerTaskStep

def _steps_for(task_id: UUID, titles: Sequence[str], current: int, detail: str) -> list[ServerTaskStep]:
    return [ServerTaskStep(
        id=uuid4(), taskId=task_id, order=index, title=title,
        status="completed" if index < current else "running" if index == current else "pending",
        detail=detail if index == current else None,
    ) for index, title in enumerate(titles)]
