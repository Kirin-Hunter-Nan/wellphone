"""Conversation request claim results and domain errors."""

from dataclasses import dataclass
from typing import Literal

class ChatRequestConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ChatRequestClaim:
    status: Literal["claimed", "processing", "completed", "conflict"]
    events: tuple[str, ...] = ()
