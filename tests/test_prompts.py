"""Unit tests for agent system prompts and prompt caching readiness."""

import pytest

from libhippo.agents.prompts import (
    BOOK_KEEPER_SYSTEM_PROMPT,
    CURATOR_SYSTEM_PROMPT,
    TASK_SOLVER_SYSTEM_PROMPT,
    VERIFIER_SYSTEM_PROMPT,
    get_agent_system_prompt,
)


def test_agent_system_prompts_exist():
    """Verify all 4 core agent system prompts are populated and structured."""
    for prompt in [
        BOOK_KEEPER_SYSTEM_PROMPT,
        CURATOR_SYSTEM_PROMPT,
        VERIFIER_SYSTEM_PROMPT,
        TASK_SOLVER_SYSTEM_PROMPT,
    ]:
        assert isinstance(prompt, str)
        assert len(prompt) > 200
        assert "LibHippo" in prompt


def test_get_agent_system_prompt_lookup():
    """Verify dynamic role lookup handles variations and aliases."""
    assert get_agent_system_prompt("book_keeper") == BOOK_KEEPER_SYSTEM_PROMPT
    assert get_agent_system_prompt("BookKeeperAgent") == BOOK_KEEPER_SYSTEM_PROMPT
    assert get_agent_system_prompt("curator") == CURATOR_SYSTEM_PROMPT
    assert get_agent_system_prompt("CuratorAgent") == CURATOR_SYSTEM_PROMPT
    assert get_agent_system_prompt("verifier") == VERIFIER_SYSTEM_PROMPT
    assert get_agent_system_prompt("VerifierAgent") == VERIFIER_SYSTEM_PROMPT
    assert get_agent_system_prompt("task_solver") == TASK_SOLVER_SYSTEM_PROMPT
    assert get_agent_system_prompt("TaskSolverAgent") == TASK_SOLVER_SYSTEM_PROMPT

    with pytest.raises(ValueError, match="Unknown agent role 'invalid_agent'"):
        get_agent_system_prompt("invalid_agent")
