"""AutoGen 0.4 Team configurations for LibHippo.

Enables assembling LibHippo agents into autonomous multi-agent teams with
safety guardrail terminations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from autogen_agentchat.conditions import MaxMessageTermination
from autogen_agentchat.teams import RoundRobinGroupChat

if TYPE_CHECKING:
    from libhippo.agents.curator import CuratorAgent
    from libhippo.agents.task_solver import TaskSolverAgent
    from libhippo.agents.verifier import VerifierAgent


def create_governance_team(
    curator: CuratorAgent,
    verifier: VerifierAgent,
    max_messages: int = 16,
) -> RoundRobinGroupChat:
    """Create an AutoGen RoundRobinGroupChat for collaborative curation and refactoring."""
    termination = MaxMessageTermination(max_messages=max_messages)
    return RoundRobinGroupChat(
        participants=[curator, verifier],
        termination_condition=termination,
    )


def create_solver_team(
    solver: TaskSolverAgent,
    verifier: VerifierAgent | None = None,
    max_messages: int = 16,
) -> RoundRobinGroupChat:
    """Create an AutoGen team for problem-solving with optional verifier collaboration."""
    participants = [solver]
    if verifier:
        participants.append(verifier)

    termination = MaxMessageTermination(max_messages=max_messages)
    return RoundRobinGroupChat(
        participants=participants,
        termination_condition=termination,
    )
