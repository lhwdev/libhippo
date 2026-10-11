"""Tests for KnowledgeHarvestObserver, record_learning, and KnowledgeHarvestSidecar."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from autogen_core.models import CreateResult, RequestUsage

from libhippo.agents.harvest_observer import KnowledgeHarvestObserver
from libhippo.orchestration.maker_checker import MakerCheckerResult
from libhippo.runner.memory import ContextMemory
from libhippo.runner.sidecar import KnowledgeHarvestSidecar
from libhippo.runner.tools.knowledge_tools import KnowledgeTools


@pytest.mark.asyncio
async def test_record_learning_queues_entry() -> None:
    tools = KnowledgeTools(
        workspace_root=Path("/fake/workspace"),
        sandbox=MagicMock(),
        project_manager=MagicMock(),
    )
    res = await tools.record_learning(
        topic="React 19 form actions",
        insight="useActionState replaces useFormState in React 19.",
        scope="common",
    )
    assert res["status"] == "queued"
    assert len(tools.harvest_queue) == 1
    assert tools.harvest_queue[0]["topic"] == "React 19 form actions"
    assert tools.harvest_queue[0]["scope"] == "common"

    defs = tools.get_tool_definitions()
    assert "record_learning" in defs
    assert defs["record_learning"].name == "record_learning"


@pytest.mark.asyncio
async def test_harvest_observer_triggers_on_novel_learnings() -> None:
    mock_typesafe_response = SimpleNamespace(
        nouls={"has_novel_learnings": SimpleNamespace(noul=0.85)},
        choices={
            "target_scope": SimpleNamespace(choice="project"),
            "knowledge_nature": SimpleNamespace(choice="critical_rule"),
        },
    )
    mock_client = AsyncMock()
    mock_client.system_one.return_value = mock_typesafe_response

    observer = KnowledgeHarvestObserver(client=mock_client, threshold=0.70)
    evaluation = await observer.evaluate({"state": "test"})

    assert evaluation.should_harvest is True
    assert evaluation.novelty_probability == 0.85
    assert evaluation.target_scope == "project"
    assert evaluation.knowledge_nature == "critical_rule"


@pytest.mark.asyncio
async def test_harvest_observer_bypasses_routine_turns() -> None:
    mock_typesafe_response = SimpleNamespace(
        nouls={"has_novel_learnings": SimpleNamespace(noul=0.25)},
        choices={
            "target_scope": SimpleNamespace(choice="project"),
            "knowledge_nature": SimpleNamespace(choice="transient_tip"),
        },
    )
    mock_client = AsyncMock()
    mock_client.system_one.return_value = mock_typesafe_response

    observer = KnowledgeHarvestObserver(client=mock_client, threshold=0.70)
    evaluation = await observer.evaluate({"state": "routine"})

    assert evaluation.should_harvest is False
    assert evaluation.novelty_probability == 0.25


@pytest.mark.asyncio
async def test_harvest_observer_triggers_on_ambiguous_tool_output() -> None:
    mock_typesafe_response = SimpleNamespace(
        nouls={
            "has_novel_learnings": SimpleNamespace(noul=0.30),
            "is_ambiguous": SimpleNamespace(noul=0.75),
        },
        choices={
            "target_scope": SimpleNamespace(choice="project"),
            "knowledge_nature": SimpleNamespace(choice="critical_rule"),
        },
    )
    mock_client = AsyncMock()
    mock_client.system_one.return_value = mock_typesafe_response

    observer = KnowledgeHarvestObserver(client=mock_client, threshold=0.70, ambiguity_threshold=0.50)
    evaluation = await observer.evaluate({"transcript": [{"role": "tool", "tool": "run_command", "is_error": True}]})

    assert evaluation.should_harvest is True
    assert evaluation.is_ambiguous is True
    assert evaluation.ambiguity_probability == 0.75
    assert evaluation.novelty_probability == 0.30


def test_extract_turn_delta_state_shortens_output_and_params() -> None:
    from libhippo.runner.harness import GeneralAgentHarness
    from libhippo.runner.config import HarnessConfig

    harness = GeneralAgentHarness(config=HarnessConfig(), model_client=MagicMock())
    harness.memory.append_user_turn("Fix bug in auth provider")
    harness.memory.append_assistant_turn(
        content="I will update auth.ts",
        thought="Inspecting auth logic and fixing issue",
        tool_calls=[
            {
                "id": "call_1",
                "name": "write_file",
                "arguments": {
                    "path": "src/auth.ts",
                    "content": "very long file content " * 100,
                    "target": "export const auth",
                },
            }
        ],
    )
    long_output = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\nLine 6"
    harness.memory.append_tool_output(
        tool_name="write_file",
        content=long_output,
        tool_call_id="call_1",
        is_error=False,
    )
    harness.memory.append_assistant_turn(content="Updated auth.")

    state = harness.extract_turn_delta_state(max_tool_lines=3)
    transcript = state["transcript"]
    assert len(transcript) == 4

    # 1. User
    assert transcript[0]["role"] == "user"
    assert "Fix bug in auth provider" in transcript[0]["content"]

    # 2. Assistant with thought & tool calls
    assert transcript[1]["role"] == "assistant"
    assert transcript[1]["thought"] == "Inspecting auth logic and fixing issue"
    assert transcript[1]["content"] == "I will update auth.ts"
    tc = transcript[1]["tool_calls"][0]
    assert tc["tool"] == "write_file"
    assert tc["params"]["path"] == "src/auth.ts"
    assert "omitted" in tc["params"]["content"]
    assert tc["params"]["target"] == "export const auth"

    # 3. Tool output
    assert transcript[2]["role"] == "tool"
    assert transcript[2]["tool"] == "write_file"
    assert "Line 1\nLine 2\nLine 3" in transcript[2]["output_preview"]
    assert "... [3 lines truncated]" in transcript[2]["output_preview"]
    assert transcript[2]["is_error"] is False

    # 4. Final assistant response
    assert transcript[3]["role"] == "assistant"
    assert transcript[3]["content"] == "Updated auth."


@pytest.mark.asyncio
async def test_harvest_sidecar_drafts_and_submits_to_governance() -> None:
    draft_markdown = """---
title: "React 19 Form Action Idiom"
namespace: "project"
status: "active"
nature: "critical_rule"
---

## Summary (Coarse View)
Always use useActionState for form pending states in React 19.

## Detailed Rules & Edge Cases (Fine View)
- Never import useFormState from react-dom.
- Ensure formAction is passed to native form.
"""
    mock_model_client = AsyncMock()
    mock_model_client.create.return_value = CreateResult(
        finish_reason="stop",
        content=draft_markdown,
        usage=RequestUsage(prompt_tokens=500, completion_tokens=150),
        cached=False,
    )

    mock_orchestrator = AsyncMock()
    mock_orchestrator.run_governance.return_value = MakerCheckerResult(
        status="COMMITTED",
        path="project/react_19_form_action_idiom.md",
        verdict="PASS",
        message="Committed",
    )

    sidecar = KnowledgeHarvestSidecar(
        model_client=mock_model_client,
        store=MagicMock(),
        orchestrator=mock_orchestrator,
    )

    memory = ContextMemory(model_name="gpt-5-nano")
    memory.set_zone1_prefix("System persona", "Workspace", [])
    memory.append_user_turn("Fix form submit")
    memory.append_assistant_turn("Fixed form submit using useActionState")

    result = await sidecar.harvest_from_context(
        parent_memory=memory,
        scope="project",
        nature="critical_rule",
        topic_hint="react form actions",
    )

    assert result.status == "COMMITTED"
    assert mock_orchestrator.run_governance.called
    call_args = mock_orchestrator.run_governance.call_args[1]
    assert "project/" in call_args["target_path"]
    assert "Summary (Coarse View)" in call_args["candidate"]
