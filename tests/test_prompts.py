"""Unit tests for agent system prompts and prompt caching readiness."""

import pytest

from libhippo.agents.prompts import (
    BOOK_KEEPER_SYSTEM_PROMPT,
    CURATOR_SYSTEM_PROMPT,
    HARNESS_SYSTEM_PROMPT,
    TASK_SOLVER_SYSTEM_PROMPT,
    VERIFIER_SYSTEM_PROMPT,
    get_agent_system_prompt,
)


def test_agent_system_prompts_exist():
    """Verify all 5 core agent system prompts are populated and structured."""
    for prompt in [
        BOOK_KEEPER_SYSTEM_PROMPT,
        CURATOR_SYSTEM_PROMPT,
        VERIFIER_SYSTEM_PROMPT,
        TASK_SOLVER_SYSTEM_PROMPT,
        HARNESS_SYSTEM_PROMPT,
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
    assert get_agent_system_prompt("harness") == HARNESS_SYSTEM_PROMPT
    assert "harvest_sidecar" in get_agent_system_prompt("harvest_sidecar") or "<SIDECAR:extract_knowledge>" in get_agent_system_prompt("harvest_sidecar")

    with pytest.raises(ValueError, match="Unknown agent role 'invalid_agent'"):
        get_agent_system_prompt("invalid_agent")


def test_harvest_sidecar_prompt_template_formatting():
    """Verify harvest sidecar markdown prompt formats with xml-like tags."""
    from libhippo.agents.prompts import HARVEST_SIDECAR_PROMPT

    assert "<SIDECAR:extract_knowledge>" in HARVEST_SIDECAR_PROMPT
    assert "</SIDECAR:extract_knowledge>" in HARVEST_SIDECAR_PROMPT
    assert "<KNOWLEDGE_HARVEST_SIDECAR>" in HARVEST_SIDECAR_PROMPT

    formatted = HARVEST_SIDECAR_PROMPT.format(
        scope="common",
        nature="critical_rule",
        topic_hint="React 19 actions",
    )
    assert "TARGET SCOPE: common" in formatted
    assert "NATURE: critical_rule" in formatted
    assert "TOPIC HINT: React 19 actions" in formatted
    assert 'namespace: "common"' in formatted
    assert 'nature: "critical_rule"' in formatted


def test_system_prompts_template_engine(tmp_path):
    """Verify system_prompts/template.py renders harness template correctly."""
    from pathlib import Path
    from libhippo.agents.system_prompts.template import (
        assemble_harness_system_prompt,
        format_tools_summary,
        format_user_rules,
        format_workspace_info,
        load_prompt_template,
        render_prompt_template,
    )

    template = load_prompt_template("harness")
    assert "<identity>" in template
    assert "{{ workspace_info }}" in template

    custom_rendered = render_prompt_template("Hello {{ name }}!", {"name": "LibHippo"})
    assert custom_rendered == "Hello LibHippo!"

    tools_summary = format_tools_summary(["read_file", "write_file", "custom_mcp_tool"])
    assert "`read_file`" in tools_summary
    assert "`custom_mcp_tool`" in tools_summary

    rules = format_user_rules({"global": "Global rule", "project": "Project rule"})
    assert "Global rule" in rules
    assert "Project rule" in rules

    ws_info = format_workspace_info(Path("/tmp/demo_workspace"))
    assert "demo_workspace" in ws_info

    full_prompt = assemble_harness_system_prompt(
        workspace_root=Path("/tmp/demo_workspace"),
        registered_tools=["read_file", "write_file"],
        agents_rules={"project": "Strict typing"},
    )
    assert "<identity>" in full_prompt
    assert "demo_workspace" in full_prompt
    assert "Strict typing" in full_prompt
    assert "`read_file`" in full_prompt

