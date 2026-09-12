"""Compatibility facade for the Agent Loop runtime.

New code should import from the focused `agent` modules. Existing composition
roots and tools may keep importing this module while the migration is gradual.
"""

from app.agent.engine import AgentGraphState, AgentLoopEngine, ToolBatchOutcome
from app.agent.errors import (
    AgentLoopBudgetExceeded,
    AgentLoopError,
    AgentLoopRepeatedFailure,
)
from app.agent.handler import AgentLoopTaskHandler
from app.agent.journal import (
    AgentLoopJournal,
    InMemoryAgentLoopJournal,
    LoopJournalEvent,
    PostgreSQLAgentLoopJournal,
)
from app.agent.model import (
    AgentLoopModel,
    LoopModelTurn,
    LoopToolCall,
    QwenAgentLoopModel,
)
from app.agent.profile import AgentTaskProfile
from app.agent.tools import (
    AgentLoopTool,
    AgentLoopToolRegistry,
    LoopToolContext,
    LoopToolResult,
)

__all__ = [
    "AgentGraphState",
    "AgentLoopBudgetExceeded",
    "AgentLoopEngine",
    "AgentLoopError",
    "AgentLoopJournal",
    "AgentLoopModel",
    "AgentLoopRepeatedFailure",
    "AgentLoopTaskHandler",
    "AgentLoopTool",
    "AgentLoopToolRegistry",
    "AgentTaskProfile",
    "InMemoryAgentLoopJournal",
    "LoopJournalEvent",
    "LoopModelTurn",
    "LoopToolCall",
    "LoopToolContext",
    "LoopToolResult",
    "PostgreSQLAgentLoopJournal",
    "QwenAgentLoopModel",
    "ToolBatchOutcome",
]
