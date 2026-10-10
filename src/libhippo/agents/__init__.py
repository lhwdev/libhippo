"""LibHippo agents package."""

from libhippo.agents.base import BaseHippoAgent
from libhippo.agents.book_keeper import BookKeeperAgent
from libhippo.agents.checker import CheckerAgent, TokenCountBoundary
from libhippo.agents.curator import CuratorAgent
from libhippo.agents.prompts import (
    BOOK_KEEPER_SYSTEM_PROMPT,
    CURATOR_SYSTEM_PROMPT,
    TASK_SOLVER_SYSTEM_PROMPT,
    VERIFIER_SYSTEM_PROMPT,
    get_agent_system_prompt,
)
from libhippo.agents.harvest_observer import HarvestEvaluation, KnowledgeHarvestObserver
from libhippo.agents.manager import AgentManager
from libhippo.agents.task_solver import TaskSolverAgent
from libhippo.agents.verifier import VerifierAgent, VerifierChildNode, VerifierDirective

__all__ = [
    "BOOK_KEEPER_SYSTEM_PROMPT",
    "CURATOR_SYSTEM_PROMPT",
    "TASK_SOLVER_SYSTEM_PROMPT",
    "VERIFIER_SYSTEM_PROMPT",
    "AgentManager",
    "BaseHippoAgent",
    "BookKeeperAgent",
    "CheckerAgent",
    "CuratorAgent",
    "HarvestEvaluation",
    "KnowledgeHarvestObserver",
    "TaskSolverAgent",
    "TokenCountBoundary",
    "VerifierAgent",
    "VerifierChildNode",
    "VerifierDirective",
    "get_agent_system_prompt",
]


