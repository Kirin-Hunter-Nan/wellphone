"""In-memory Tool Result store used by tests and local composition."""

from __future__ import annotations

from uuid import UUID

from app.api.protocol import ToolResultSubmission
from app.providers.base import ProviderToolCallContext
from app.tool_results.canonical import _canonical_payload
from app.tool_results.models import ContinuationClaim, ToolResultConflictError

class InMemoryToolResultStore:
    def __init__(self) -> None:
        self._results: dict[tuple[UUID, str], str] = {}
        self._contexts: dict[tuple[UUID, str], ProviderToolCallContext] = {}
        self._replies: dict[tuple[UUID, str], str] = {}
        self._continuation_statuses: dict[tuple[UUID, str], str] = {}

    async def initialize(self) -> None:
        pass

    async def is_healthy(self) -> bool:
        return True

    async def record(
        self,
        conversation_id: UUID,
        result: ToolResultSubmission,
    ) -> bool:
        key = (conversation_id, result.tool_call_id)
        payload_json = _canonical_payload(result)
        existing = self._results.get(key)
        if existing is None:
            self._results[key] = payload_json
            return True
        if existing != payload_json:
            raise ToolResultConflictError(
                "A different result already exists for this Tool call"
            )
        return False

    async def save_tool_call_context(
        self,
        conversation_id: UUID,
        context: ProviderToolCallContext,
    ) -> None:
        key = (conversation_id, context.tool_call_id)
        existing = self._contexts.get(key)
        if existing is None:
            self._contexts[key] = context
            self._continuation_statuses[key] = "pending"
            return
        if existing != context:
            raise ToolResultConflictError(
                "A different context already exists for this Tool call"
            )

    async def get_tool_call_context(
        self,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> ProviderToolCallContext | None:
        return self._contexts.get((conversation_id, tool_call_id))

    async def get_assistant_reply(
        self,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> str | None:
        return self._replies.get((conversation_id, tool_call_id))

    async def claim_continuation(
        self,
        conversation_id: UUID,
        tool_call_id: str,
        *,
        lease_seconds: int,
    ) -> ContinuationClaim:
        del lease_seconds
        key = (conversation_id, tool_call_id)
        if key not in self._contexts:
            return ContinuationClaim(status="missing")
        reply = self._replies.get(key)
        if reply is not None:
            return ContinuationClaim(status="completed", assistant_reply=reply)
        status = self._continuation_statuses.get(key, "pending")
        if status == "processing":
            return ContinuationClaim(status="processing")
        self._continuation_statuses[key] = "processing"
        return ContinuationClaim(status="claimed")

    async def save_assistant_reply(
        self,
        conversation_id: UUID,
        tool_call_id: str,
        reply: str,
    ) -> None:
        key = (conversation_id, tool_call_id)
        if key not in self._contexts:
            raise ToolResultConflictError("Tool call context does not exist")
        existing = self._replies.get(key)
        if existing is None:
            self._replies[key] = reply
            self._continuation_statuses[key] = "completed"
            return
        if existing != reply:
            raise ToolResultConflictError(
                "A different assistant reply already exists for this Tool call"
            )

    async def fail_continuation(
        self,
        conversation_id: UUID,
        tool_call_id: str,
        error_code: str,
    ) -> None:
        del error_code
        key = (conversation_id, tool_call_id)
        if key in self._contexts and key not in self._replies:
            self._continuation_statuses[key] = "failed"

    async def close(self) -> None:
        pass
