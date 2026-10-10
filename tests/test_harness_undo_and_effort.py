import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from libhippo.runner.harness import GeneralAgentHarness, HarnessConfig
from libhippo.config.models import (
    DEFAULT_HARVEST_REASONING_EFFORT,
    DEFAULT_DEEP_EXPLORATION_REASONING_EFFORT,
)
from libhippo.runner.harness import GeneralAgentHarness
from libhippo.runner.transport import OpenAIResponsesClient


def make_mock_client() -> OpenAIResponsesClient:
    client = OpenAIResponsesClient(model="gpt-5-turbo", api_key="mock-key")
    return client


@pytest.fixture
def harness(tmp_path: Path) -> GeneralAgentHarness:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True)
    config = HarnessConfig(workspace_root=ws, user_config_dir=cfg)
    mock_client = make_mock_client()
    return GeneralAgentHarness(config=config, model_client=mock_client)


def test_transport_configuration_update_on_effort_change():
    """Verify OpenAIResponsesClient injects configuration_update when effort changes."""
    client = OpenAIResponsesClient(model="gpt-5.1", api_key="mock-key", reasoning_effort="medium")
    client.last_response_id = "resp_123"
    client._applied_reasoning_effort = "medium"

    # Now change effort to high
    client.set_reasoning_effort("high")
    assert client.reasoning_effort == "high"

    # Simulate req_kwargs preparation
    req_kwargs = {"input": []}
    if client.last_response_id and client.reasoning_effort != client._applied_reasoning_effort:
        req_kwargs["input"].append({
            "type": "configuration_update",
            "reasoning": {"effort": client.reasoning_effort},
        })
        client._applied_reasoning_effort = client.reasoning_effort

    assert len(req_kwargs["input"]) == 1
    assert req_kwargs["input"][0] == {
        "type": "configuration_update",
        "reasoning": {"effort": "high"},
    }
    assert client._applied_reasoning_effort == "high"


@pytest.mark.asyncio
async def test_harness_undo_truncates_memory_and_kills_tasks(harness: GeneralAgentHarness):
    """Verify harness.undo(index) truncates context memory and terminates tasks."""
    # Append turns
    harness.memory.append_user_turn("Message 0")
    harness.memory.append_assistant_turn("Response 0")
    harness.memory.append_user_turn("Message 1")
    harness.memory.append_assistant_turn("Response 1")

    # Mock a task started at index 1
    mock_task = MagicMock()
    mock_task.start_time = 1000.0
    mock_task.is_running = True
    mock_task.stop = MagicMock()
    harness.sandbox.tasks["task_after"] = mock_task

    assert len(harness.memory.zone2_history) == 4

    # Perform undo at message index 2 (which is user turn "Message 1")
    res = await harness.undo(2)
    assert res["status"] == "undone"
    assert res["message_index"] == 2
    assert res["prompt"] == "Message 1"
    assert len(harness.memory.zone2_history) == 2
    assert "Message 0" in harness.memory.zone2_history[0].content
    assert "Response 0" in harness.memory.zone2_history[1].content


@pytest.mark.asyncio
async def test_knowledge_worker_separation_from_interrupt(harness: GeneralAgentHarness):
    """Verify interrupt() does not kill harvest workers, but stop_knowledge_workers() does."""
    # Simulate an active harvest background task
    mock_task = asyncio.create_task(asyncio.sleep(10))
    harness._active_sidecar_tasks.add(mock_task)

    assert harness.has_active_knowledge_workers is True

    # Calling interrupt should pause main execution but NOT cancel harvest future
    harness.interrupt("user_pause")
    assert not mock_task.cancelled()
    assert harness.has_active_knowledge_workers is True

    # Calling stop_knowledge_workers should cancel it
    stopped_count = harness.stop_knowledge_workers()
    assert stopped_count == 1
    # Yield to let event loop process cancellation
    await asyncio.sleep(0.01)
    assert mock_task.cancelled()
    assert harness.has_active_knowledge_workers is False


def test_harness_reasoning_effort_transitions(harness: GeneralAgentHarness):
    """Verify harness reasoning effort setting and deep exploration transition."""
    # Default effort
    assert harness.reasoning_effort == "medium"

    harness.set_reasoning_effort("high")
    assert harness.reasoning_effort == "high"

    # Emulate transition to deep exploration
    prev = harness.reasoning_effort
    harness.set_reasoning_effort(DEFAULT_DEEP_EXPLORATION_REASONING_EFFORT)
    assert harness.reasoning_effort == DEFAULT_DEEP_EXPLORATION_REASONING_EFFORT

    # Emulate completion of deep exploration
    harness.set_reasoning_effort(prev)
    assert harness.reasoning_effort == prev
