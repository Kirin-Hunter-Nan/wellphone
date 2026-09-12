"""Persistence contract for device Tool Results and model continuation."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.api.protocol import ToolResultSubmission
from app.providers.base import ProviderToolCallContext
from app.tool_results.models import ContinuationClaim

class ToolResultStore(Protocol):
    async def initialize(self) -> None: ...
    async def is_healthy(self) -> bool: ...
    async def record(
        self,
        conversation_id: UUID,
        result: ToolResultSubmission,
    ) -> bool: ...
    async def save_tool_call_context(
        self,
        conversation_id: UUID,
        context: ProviderToolCallContext,
    ) -> None: ...
    async def get_tool_call_context(
        self,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> ProviderToolCallContext | None: ...
    async def get_assistant_reply(
        self,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> str | None: ...
    async def claim_continuation(
        self,
        conversation_id: UUID,
        tool_call_id: str,
        *,
        lease_seconds: int,
    ) -> ContinuationClaim: ...
    async def save_assistant_reply(
        self,
        conversation_id: UUID,
        tool_call_id: str,
        reply: str,
    ) -> None: ...
    async def fail_continuation(
        self,
        conversation_id: UUID,
        tool_call_id: str,
        error_code: str,
    ) -> None: ...
    async def close(self) -> None: ...
