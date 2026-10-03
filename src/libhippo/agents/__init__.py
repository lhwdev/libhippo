"""LibHippo agents package."""

from libhippo.agents.base import BaseHippoAgent
from libhippo.agents.checker import CheckerAgent, TokenCountBoundary
from libhippo.agents.prompts import (
    BOOK_KEEPER_SYSTEM_PROMPT,
    CURATOR_SYSTEM_PROMPT,
    TASK_SOLVER_SYSTEM_PROMPT,
    VERIFIER_SYSTEM_PROMPT,
    get_agent_system_prompt,
)

__all__ = [
    "BOOK_KEEPER_SYSTEM_PROMPT",
    "CURATOR_SYSTEM_PROMPT",
    "TASK_SOLVER_SYSTEM_PROMPT",
    "VERIFIER_SYSTEM_PROMPT",
    "BaseHippoAgent",
    "CheckerAgent",
    "TokenCountBoundary",
    "get_agent_system_prompt",
]


