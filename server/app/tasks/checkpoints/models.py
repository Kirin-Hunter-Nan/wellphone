"""Checkpoint persistence values and domain errors."""

from dataclasses import dataclass


class TaskCheckpointConflictError(ValueError):
    """Raised when an idempotency key identifies different checkpoint content."""


@dataclass(frozen=True, slots=True)
class TaskCheckpointRecord:
    duplicate: bool
    applied: bool
    current_revision: int
    gap: bool
