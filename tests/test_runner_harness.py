"""Unit and integration tests for GeneralAgentHarness and TaskSolverAgent integration."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from autogen_core.models import CreateResult, RequestUsage

from libhippo.agents.task_solver import TaskSolverAgent
from libhippo.runner.config import HarnessConfig
from libhippo.runner.harness import GeneralAgentHarness
from libhippo.runner.types import (
    PhaseTransitionEvent,
    TokenChunkEvent,
    TurnCompletedEvent,
)
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.retrieval import KnowledgeDispatcher


def make_mock_client(content: str) -> AsyncMock:
    """Helper to create a mock ChatCompletionClient returning a specific text response."""
    client = AsyncMock()
    client.create.return_value = CreateResult(
        finish_reason="stop",
        content=content,
        usage=RequestUsage(prompt_tokens=30, completion_tokens=20),
        cached=False,
    )
    return client


@pytest.mark.asyncio
async def test_harness_5_phase_lifecycle_and_events(tmp_path: Path):
    """Test full 5-phase harness lifecycle streaming events and session persistence."""
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir(parents=True)
    cfg_dir = tmp_path / "cfg"

    config = HarnessConfig(
        workspace_root=ws_dir,
        user_config_dir=cfg_dir,
        project_id="test_lifecycle_proj",
    )
    client = make_mock_client("def fibonacci(n): return n if n <= 1 else fibonacci(n-1) + fibonacci(n-2)")
    harness = GeneralAgentHarness(config=config, model_client=client)

    events = []
    async for event in harness.run("Implement a fibonacci function"):
        events.append(event)

    # Check phase transitions
    phase_events = [e for e in events if isinstance(e, PhaseTransitionEvent)]
    phases = [p.to_phase for p in phase_events]
    assert "alignment" in phases
    assert "planning" in phases
    assert "implementation" in phases
    assert "review" in phases
    assert "maintenance" in phases

    # Check token chunk streaming
    token_events = [e for e in events if isinstance(e, TokenChunkEvent)]
    assert any("fibonacci" in t.delta for t in token_events)

    # Check turn completion
    turn_events = [e for e in events if isinstance(e, TurnCompletedEvent)]
    assert len(turn_events) == 1
    assert turn_events[0].turn_index == 1
    assert "fibonacci" in turn_events[0].response
    assert turn_events[0].duration_seconds >= 0

    # Verify session transcript file was written
    transcript_file = harness.session.session_dir / "transcript.jsonl"
    assert transcript_file.exists()
    transcript_content = transcript_file.read_text(encoding="utf-8")
    assert "fibonacci" in transcript_content


@pytest.mark.asyncio
async def test_harness_sidecar_query(tmp_path: Path):
    """Test sidecar /btw concurrent query execution without mutating parent turn state."""
    config = HarnessConfig(
        workspace_root=tmp_path / "workspace",
        user_config_dir=tmp_path / "cfg",
    )
    config.workspace_root.mkdir(parents=True)

    client = make_mock_client("The user previously requested a data pipeline refactoring.")
    harness = GeneralAgentHarness(config=config, model_client=client)

    # Seed parent memory with history
    harness.memory.append_user_turn("Refactor data pipeline to use generators", timestamp="2026-10-03T10:00:00Z")
    parent_turn_count = harness.governor.current_turns
    parent_msg_count = len(harness.memory.get_all_messages())

    # Execute sidecar query
    answer = await harness.ask_sidecar("What was the user's intent?")
    assert "data pipeline refactoring" in answer

    # Verify parent state remained undisturbed
    assert harness.governor.current_turns == parent_turn_count
    assert len(harness.memory.get_all_messages()) == parent_msg_count


@pytest.mark.asyncio
async def test_harness_skill_discovery_and_invocation(tmp_path: Path):
    """Test discovering and invoking a skill defined in .agents/skills."""
    ws_dir = tmp_path / "workspace"
    skill_dir = ws_dir / ".agents" / "skills" / "deploy-checker"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: deploy-checker\ndescription: Verifies deployment readiness\n---\nCheck build artifacts before shipping.",
        encoding="utf-8",
    )

    config = HarnessConfig(
        workspace_root=ws_dir,
        user_config_dir=tmp_path / "cfg",
    )
    client = make_mock_client("Deployment status: All artifacts validated successfully.")
    harness = GeneralAgentHarness(config=config, model_client=client)

    assert "deploy-checker" in harness.skills
    assert harness.skills["deploy-checker"].name == "deploy-checker"

    result = await harness.invoke_skill("deploy-checker", {"env": "production"})
    assert "Deployment status" in result


@pytest.mark.asyncio
async def test_harness_interrupt(tmp_path: Path):
    """Test harness interrupt pauses execution and appends interrupt event."""
    config = HarnessConfig(
        workspace_root=tmp_path / "workspace",
        user_config_dir=tmp_path / "cfg",
    )
    config.workspace_root.mkdir(parents=True)

    harness = GeneralAgentHarness(config=config, model_client=make_mock_client("Done"))
    assert not harness._is_paused

    harness.interrupt()
    assert harness._is_paused
    assert harness._interrupt_event.is_set()

    messages = harness.memory.get_all_messages()
    assert any("<interrupt_event" in m.content for m in messages)


@pytest.mark.asyncio
async def test_harness_knowledge_bridge_and_maintenance(tmp_path: Path):
    """Test knowledge store integration: query_knowledge tool presence and post-task maintenance."""
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir(parents=True)
    knowledge_dir = tmp_path / "knowledge"

    async with KnowledgeStore(root_dir=knowledge_dir) as store:
        dispatcher = KnowledgeDispatcher(store=store)
        config = HarnessConfig(
            workspace_root=ws_dir,
            user_config_dir=tmp_path / "cfg",
        )
        harness = GeneralAgentHarness(
            config=config,
            store=store,
            dispatcher=dispatcher,
            model_client=make_mock_client("OK"),
        )

        assert "query_knowledge" in harness.tools.tools
        tool_def = harness.tools.tools["query_knowledge"]
        assert tool_def.handler is not None

        # Execute query_knowledge handler
        result = await tool_def.handler(query="authentication token handling")
        assert isinstance(result, dict)
        assert "status" in result

        # Trigger maintenance
        await harness.post_task_maintenance()


@pytest.mark.asyncio
async def test_task_solver_agent_harness_integration(tmp_path: Path):
    """Test TaskSolverAgent delegation to GeneralAgentHarness."""
    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir(parents=True)
    cfg_dir = tmp_path / "cfg"

    config = HarnessConfig(
        workspace_root=ws_dir,
        user_config_dir=cfg_dir,
    )
    client = make_mock_client("Refactored algorithm using dynamic programming.")
    agent = TaskSolverAgent(
        model_client=client,
        harness_config=config,
    )
    assert agent.harness is not None

    solution = await agent.solve("Optimize knapsack solver")
    assert "dynamic programming" in solution

    # Test event streaming via run_harness
    events = []
    async for ev in agent.run_harness("Second run"):
        events.append(ev)
    assert any(isinstance(e, TurnCompletedEvent) for e in events)


@pytest.mark.asyncio
async def test_harness_steer_and_websocket_interrupt(tmp_path: Path):
    """Test mid-turn steering and interrupt with steering guidance."""
    config = HarnessConfig(
        workspace_root=tmp_path / "workspace",
        user_config_dir=tmp_path / "cfg",
    )
    config.workspace_root.mkdir(parents=True)

    client = make_mock_client("Processing")
    client.steer = AsyncMock(return_value={"status": "steered_mock"})
    client.cancel = AsyncMock(return_value={"status": "cancelled_mock"})

    harness = GeneralAgentHarness(config=config, model_client=client)

    # Test steer
    steer_res = await harness.steer("Change to SQLite database")
    assert steer_res["status"] == "steered_mock"
    client.steer.assert_called_with("Change to SQLite database")

    mem_msgs = harness.memory.get_all_messages()
    assert any('<steer_event guidance="Change to SQLite database"/>' in m.content for m in mem_msgs)

    # Test interrupt with steer_text
    harness.interrupt(steer_text="Switch to in-memory mode")
    assert harness._is_paused
    # Allow background task to schedule
    await asyncio.sleep(0.01)
    client.steer.assert_called_with("Switch to in-memory mode")

    int_msgs = harness.memory.get_all_messages()
    assert any("<interrupt_event" in m.content for m in int_msgs)


@pytest.mark.asyncio
async def test_openai_responses_websocket_client_fallback_and_steer():
    """Test OpenAIResponsesWebSocketClient steer, cancel, and HTTP fallback."""
    from libhippo.runner.transport import OpenAIResponsesWebSocketClient

    client = OpenAIResponsesWebSocketClient(
        model="gpt-4o",
        api_key="mock-test-key",
        enable_http_fallback=True,
    )
    assert not client._is_connected

    # Steer queues when not yet connected to live WS
    res_steer = await client.steer("Test steering")
    assert res_steer["status"] in ("steer_queued", "steered_over_websocket")

    # Cancel handles locally when offline
    res_cancel = await client.cancel()
    assert res_cancel["status"] in ("cancelled_local", "cancelled_over_websocket")

    await client.close()

