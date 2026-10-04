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


@pytest.mark.asyncio
async def test_openai_responses_websocket_client_tool_calling_and_delta():
    """Verify OpenAIResponsesWebSocketClient transmits tools, parses tool calls on response.completed, and chains deltas."""
    from autogen_core import FunctionCall
    from autogen_core.models import (
        AssistantMessage,
        FunctionExecutionResult,
        FunctionExecutionResultMessage,
        SystemMessage,
        UserMessage,
    )
    from libhippo.runner.transport import OpenAIResponsesWebSocketClient

    client = OpenAIResponsesWebSocketClient(
        model="gpt-6.1-sol",
        api_key="mock-key",
        enable_http_fallback=False,
    )

    from typing import Any

    sent_events: list[dict] = []

    class MockEvent:
        def __init__(self, type: str, response: Any = None):
            self.type = type
            self.response = response

    class MockResponse:
        def __init__(self, id_: str, output: list, usage: Any = None):
            self.id = id_
            self.output = output
            self.usage = usage

    class MockItem:
        def __init__(self, type: str, **kwargs):
            self.type = type
            for k, v in kwargs.items():
                setattr(self, k, v)

    class MockConnection:
        def __init__(self, events_to_yield: list):
            self.events = events_to_yield

        async def send(self, data: dict):
            sent_events.append(data)

        async def __aiter__(self):
            for e in self.events:
                yield e

    mock_resp1 = MockResponse(
        id_="resp_first_turn",
        output=[MockItem(type="function_call", id="call_list_1", name="list_dir", arguments='{"dir_path": "."}')],
    )
    conn1 = MockConnection([
        MockEvent("response.created", response=mock_resp1),
        MockEvent("response.completed", response=mock_resp1),
    ])

    client._connection = conn1
    client._is_connected = True

    messages_turn1 = [
        SystemMessage(content="You are a coding assistant."),
        UserMessage(content="Summarize what is in this project.", source="user"),
    ]
    tools = [{"name": "list_dir", "description": "List directory", "parameters": {}}]

    result1 = await client.create(messages=messages_turn1, tools=tools)

    # 1. Assert sent_events has response.create with model, instructions, input, tools
    assert len(sent_events) == 1
    create_payload = sent_events[0]
    assert create_payload["type"] == "response.create"
    assert create_payload["model"] == "gpt-6.1-sol"
    assert create_payload["instructions"] == "You are a coding assistant."
    assert len(create_payload["input"]) == 1
    assert create_payload["input"][0]["content"] == "Summarize what is in this project."
    assert len(create_payload["tools"]) == 1
    assert create_payload["tools"][0]["name"] == "list_dir"
    assert "previous_response_id" not in create_payload

    # 2. Assert result1 parsed tool call instead of returning empty stop
    assert result1.finish_reason == "function_calls"
    assert isinstance(result1.content, list)
    assert len(result1.content) == 1
    assert isinstance(result1.content[0], FunctionCall)
    assert result1.content[0].name == "list_dir"
    assert client.last_response_id == "resp_first_turn"

    # Turn 2: Delta chaining
    sent_events.clear()
    mock_resp2 = MockResponse(
        id_="resp_second_turn",
        output=[MockItem(type="message", content="The project contains libhippo package.")],
    )
    conn2 = MockConnection([
        MockEvent("response.created", response=mock_resp2),
        MockEvent("response.completed", response=mock_resp2),
    ])
    client._connection = conn2

    messages_turn2 = [
        SystemMessage(content="You are a coding assistant."),
        UserMessage(content="Summarize what is in this project.", source="user"),
        AssistantMessage(content=result1.content, source="assistant"),
        FunctionExecutionResultMessage(content=[FunctionExecutionResult(call_id="call_list_1", content="README.md, src, tests", name="list_dir")]),
    ]

    result2 = await client.create(messages=messages_turn2, tools=tools)

    assert len(sent_events) == 1
    create_payload2 = sent_events[0]
    assert create_payload2["type"] == "response.create"
    assert create_payload2["previous_response_id"] == "resp_first_turn"
    assert "instructions" not in create_payload2
    # Input should ONLY contain the delta function_call_output!
    assert len(create_payload2["input"]) == 1
    assert create_payload2["input"][0]["type"] == "function_call_output"
    assert create_payload2["input"][0]["call_id"] == "call_list_1"

    assert result2.finish_reason == "stop"
    assert result2.content == "The project contains libhippo package."
    assert client.last_response_id == "resp_second_turn"

    await client.close()


@pytest.mark.asyncio
async def test_harness_multiple_tool_invocations_turn(tmp_path: Path):
    """Verify that multiple tool invocations in a single turn execute all tools and batch results into one message."""
    from autogen_core import FunctionCall
    from autogen_core.models import (
        AssistantMessage,
        ChatCompletionClient,
        CreateResult,
        FunctionExecutionResultMessage,
        LLMMessage,
        ModelCapabilities,
        ModelInfo,
        RequestUsage,
    )
    from libhippo.runner.types import ToolCallResultEvent, ToolCallStartEvent

    ws_dir = tmp_path / "workspace"
    ws_dir.mkdir(parents=True)
    cfg_dir = tmp_path / "cfg"
    (ws_dir / "file1.txt").write_text("Hello from file 1")
    (ws_dir / "file2.txt").write_text("Hello from file 2")

    config = HarnessConfig(
        workspace_root=ws_dir,
        user_config_dir=cfg_dir,
    )

    received_messages: list[list[LLMMessage]] = []

    class MockMultiToolClient(ChatCompletionClient):
        def __init__(self):
            self.turn = 0
            self._info = ModelInfo(vision=True, function_calling=True, json_output=True, family="unknown")

        @property
        def model_info(self) -> ModelInfo:
            return self._info

        @property
        def capabilities(self) -> ModelCapabilities:
            return {"vision": True, "function_calling": True, "json_output": True}

        def actual_usage(self) -> RequestUsage:
            return RequestUsage(prompt_tokens=10, completion_tokens=10)

        def total_usage(self) -> RequestUsage:
            return RequestUsage(prompt_tokens=10, completion_tokens=10)

        def count_tokens(self, messages, tools=[]):
            return 10

        def remaining_tokens(self, messages, tools=[]):
            return 1000

        async def close(self):
            pass

        async def create_stream(self, messages, tools=[], **kwargs):
            res = await self.create(messages, tools=tools, **kwargs)
            yield res

        async def create(self, messages, tools=[], **kwargs):
            received_messages.append(list(messages))
            self.turn += 1
            if self.turn == 1:
                return CreateResult(
                    finish_reason="function_calls",
                    content=[
                        FunctionCall(id="call_f1", name="read_file", arguments='{"path": "file1.txt"}'),
                        FunctionCall(id="call_f2", name="read_file", arguments='{"path": "file2.txt"}'),
                    ],
                    usage=RequestUsage(prompt_tokens=10, completion_tokens=10),
                    cached=False,
                )
            else:
                return CreateResult(
                    finish_reason="stop",
                    content="Both files read successfully.",
                    usage=RequestUsage(prompt_tokens=20, completion_tokens=10),
                    cached=False,
                )

    client = MockMultiToolClient()
    harness = GeneralAgentHarness(config=config, model_client=client)

    events = []
    async for event in harness.stream("Read both files"):
        events.append(event)

    start_events = [e for e in events if isinstance(e, ToolCallStartEvent)]
    assert len(start_events) == 2
    assert [e.tool_call_id for e in start_events] == ["call_f1", "call_f2"]

    result_events = [e for e in events if isinstance(e, ToolCallResultEvent)]
    assert len(result_events) == 2
    assert [e.tool_call_id for e in result_events] == ["call_f1", "call_f2"]
    assert "Hello from file 1" in str(result_events[0].result)
    assert "Hello from file 2" in str(result_events[1].result)

    assert len(received_messages) == 2
    turn2_msgs = received_messages[1]
    last_msg = turn2_msgs[-1]
    assert isinstance(last_msg, FunctionExecutionResultMessage)
    assert len(last_msg.content) == 2
    assert last_msg.content[0].call_id == "call_f1"
    assert last_msg.content[1].call_id == "call_f2"
    assert "Hello from file 1" in last_msg.content[0].content
    assert "Hello from file 2" in last_msg.content[1].content



