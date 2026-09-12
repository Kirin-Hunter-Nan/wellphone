"""Provider-neutral results produced by the conversation intent layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from app.api.protocol import ToolRequest


@dataclass(frozen=True, slots=True)
class ChatIntent:
    kind: Literal["chat"] = "chat"


@dataclass(frozen=True, slots=True)
class ClarifyIntent:
    question: str
    capability: str
    missing_fields: tuple[str, ...]
    kind: Literal["clarify"] = "clarify"


@dataclass(frozen=True, slots=True)
class ActionIntent:
    request: ToolRequest
    kind: Literal["action"] = "action"


IntentResolution: TypeAlias = ChatIntent | ClarifyIntent | ActionIntent
