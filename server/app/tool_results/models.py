"""Tool-result continuation claims and domain errors."""

from dataclasses import dataclass
from typing import Literal

class ToolResultConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ContinuationClaim:
    status: Literal["claimed", "processing", "completed", "missing"]
    assistant_reply: str | None = None
