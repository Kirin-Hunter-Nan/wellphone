"""Capability-specific policy and completion hooks for the Agent Loop."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json

from app.tasks.models import ServerTask, TaskOutcome


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
