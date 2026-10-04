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

    memory = ContextMemory()
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
