"""Errors raised by the Agent Loop runtime."""

class AgentLoopError(RuntimeError):
    retryable = False

    pass


class AgentLoopBudgetExceeded(AgentLoopError):
    pass


class AgentLoopRepeatedFailure(AgentLoopError):
    pass
