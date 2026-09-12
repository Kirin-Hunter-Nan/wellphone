"""Compatibility facade for Tool Result persistence."""

from app.tool_results.models import ContinuationClaim, ToolResultConflictError
from app.tool_results.repository import ToolResultStore
from app.tool_results.stores.memory import InMemoryToolResultStore
from app.tool_results.stores.postgres import PostgreSQLToolResultStore

__all__ = [
    "ContinuationClaim",
    "InMemoryToolResultStore",
    "PostgreSQLToolResultStore",
    "ToolResultConflictError",
    "ToolResultStore",
]
