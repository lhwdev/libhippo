"""Tests for ResourceDiscovery and ConversationSession."""

import asyncio
from pathlib import Path
import pytest

from libhippo.runner.config import HarnessConfig
from libhippo.runner.discovery import ResourceDiscovery
from libhippo.runner.persistence import ConversationSession, context_messages_to_chat_messages
from libhippo.runner.types import ContextMessage


def test_resource_discovery(tmp_path: Path):
    """Test automatic discovery of .libhippo, AGENTS.md, and skills."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    user_cfg = tmp_path / "user_config"
    user_cfg.mkdir()

    # Create .libhippo dir
    libhippo_dir = ws / ".libhippo"
    libhippo_dir.mkdir()

    # Create Project AGENTS.md
    (ws / "AGENTS.md").write_text("# Project Guidelines", encoding="utf-8")
    # Create Global AGENTS.md
    (user_cfg / "AGENTS.md").write_text("# Global Guidelines", encoding="utf-8")

    # Create a project skill conforming to .agents/skills/<name>/SKILL.md
    skill_dir = ws / ".agents" / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: A test skill\n---\n## Instructions\nDo test.",
        encoding="utf-8",
    )

    cfg = HarnessConfig(workspace_root=ws, user_config_dir=user_cfg)
    disco = ResourceDiscovery(workspace_root=ws, config=cfg)

    # 1. Discover .libhippo
    assert disco.discover_libhippo_dir() == libhippo_dir

    # 2. Discover AGENTS.md
    rules = disco.discover_agents_markdown()
    assert "# Project Guidelines" in rules["project"]
    assert "# Global Guidelines" in rules["global"]

    # 3. Discover skills
    skills = disco.discover_skills()
    assert "my-skill" in skills
    assert skills["my-skill"].description == "A test skill"
    assert "Do test." in skills["my-skill"].system_prompt


def test_env_hierarchy_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test loading env from available combination of .env.local, .env.<env>.local, etc."""
    ws = tmp_path / "workspace"
    ws.mkdir()

    # Clear test env vars
    monkeypatch.delenv("TEST_VAR_A", raising=False)
    monkeypatch.delenv("TEST_VAR_B", raising=False)
    monkeypatch.delenv("TEST_VAR_C", raising=False)
    monkeypatch.delenv("TEST_VAR_D", raising=False)

    # 1. Base .env
    (ws / ".env").write_text("TEST_VAR_A=base_a\nTEST_VAR_B=base_b\nTEST_VAR_C=base_c\nTEST_VAR_D=base_d\n", encoding="utf-8")
    # 2. Environment specific .env.development
    (ws / ".env.development").write_text("TEST_VAR_B=dev_b\nTEST_VAR_C=dev_c\n", encoding="utf-8")
    # 3. General local .env.local
    (ws / ".env.local").write_text("TEST_VAR_C=local_c\n", encoding="utf-8")
    # 4. Environment local .env.development.local
    (ws / ".env.development.local").write_text("TEST_VAR_D=dev_local_d\n", encoding="utf-8")

    cfg = HarnessConfig(workspace_root=ws)
    disco = ResourceDiscovery(workspace_root=ws, config=cfg)
    loaded_files = disco.load_env(environment="development")

    assert len(loaded_files) == 4
    import os
    assert os.environ.get("TEST_VAR_A") == "base_a"
    assert os.environ.get("TEST_VAR_B") == "dev_b"
    assert os.environ.get("TEST_VAR_C") == "local_c"
    assert os.environ.get("TEST_VAR_D") == "dev_local_d"


@pytest.mark.asyncio
async def test_conversation_persistence(tmp_path: Path):
    """Test ConversationSession persists messages, metadata, artifacts, and tasks on demand."""
    storage = tmp_path / "session_storage"
    session = ConversationSession(
        project_id="test-proj",
        conversation_id="conv-123",
        storage_dir=storage,
    )

    # Verify metadata created and on-demand folders not yet created
    assert session.metadata_file.exists()
    assert session.get_metadata()["id"] == "conv-123"
    assert not (storage / "artifacts").exists()
    assert not (storage / "tasks").exists()
    assert not (storage / "subagents").exists()

    # 1. Append message and verify automatic naming
    msg = ContextMessage(
        role="user",
        content="Implement feature X",
        zone="zone2_linear",
        raw_token_count=10,
    )
    await session.append_message(msg)
    loaded = session.get_messages()
    assert len(loaded) == 1
    assert loaded[0].content == "Implement feature X"
    assert session.get_name() == "Implement feature X"
    assert session.get_metadata()["name"] == "Implement feature X"
    assert session.get_metadata()["message_count"] == 1

    # Verify extraction from USER_PROMPT tag
    tag_prompt = (
        "System preamble outside prompt\n"
        '<USER_PROMPT timestamp="2026-10-10T09:32:45.885526+00:00" session_elapsed="0s" branch="main">\n'
        "Hello?\n"
        "</USER_PROMPT>\n"
        "Trailing text outside prompt"
    )
    assert session._extract_name_from_user_prompt(tag_prompt) == "Hello?"
    chat_list = context_messages_to_chat_messages([
        ContextMessage(role="user", content=tag_prompt, zone="zone2_linear", raw_token_count=10)
    ])
    assert len(chat_list) == 1
    assert chat_list[0]["content"] == "Hello?"
    assert chat_list[0]["timestamp"] == "09:32:45"

    # 2. Record artifact (creates artifacts folder on demand)
    art_path = await session.record_artifact(
        name="plan.md",
        content="# Plan\n1. Do X",
        metadata={"Summary": "Test plan", "UserFacing": True},
    )
    assert art_path.exists()
    assert (storage / "artifacts").is_dir()
    artifacts = session.list_artifacts()
    assert len(artifacts) == 1
    assert artifacts[0]["name"] == "plan.md"
    assert artifacts[0]["metadata"]["UserFacing"] is True

    # 3. Record task and subagent (creates folders on demand)
    await session.record_task("task-1", {"status": "completed", "exit_code": 0})
    assert (storage / "tasks").is_dir()

    await session.record_subagent("sub-1", {"role": "reviewer", "state": "idle"})
    assert (storage / "subagents").is_dir()

    subagents = session.list_subagents()
    assert len(subagents) == 1
    assert subagents[0]["role"] == "reviewer"


def test_context_messages_turn_grouping_and_sequential_items():
    """Verify that context_messages_to_chat_messages groups assistant steps into one turn,
    orders items sequentially according to time, preserves reasoning, and preserves knowledge events."""
    msgs = [
        ContextMessage(
            role="user",
            content="<USER_PROMPT timestamp=\"2026-10-11T12:00:00+00:00\">Search knowledge and edit file</USER_PROMPT>",
            zone="zone2_linear",
        ),
        # Intermediate step 1: Reasoning + tool call 1 (query_knowledge)
        ContextMessage(
            role="assistant",
            content="[Tool calls: query_knowledge]",
            zone="zone2_linear",
            metadata={
                "thought": "I should first consult the knowledge base.",
                "tool_calls": [{"id": "call_1", "name": "query_knowledge", "arguments": "{\"query\": \"testing\"}"}],
                "timestamp": "12:00:01",
            },
        ),
        # Tool output 1 with knowledge events emitted during execution
        ContextMessage(
            role="tool",
            content="Found knowledge: testing patterns",
            zone="zone2_linear",
            tool_call_id="call_1",
            metadata={
                "tool_name": "query_knowledge",
                "is_error": False,
                "knowledge_events": [
                    {
                        "agent": "CuratorAgent",
                        "action": "drafting",
                        "status": "completed",
                        "output_summary": "Retrieved draft",
                        "target_path": "common/testing.md",
                    },
                    {
                        "agent": "CheckerAgent",
                        "action": "audit",
                        "status": "passed",
                        "output_summary": "Audit passed",
                        "target_path": "common/testing.md",
                    },
                ],
            },
        ),
        # Intermediate step 2: Reasoning + tool call 2 (write_file)
        ContextMessage(
            role="assistant",
            content="[Tool calls: write_file]",
            zone="zone2_linear",
            metadata={
                "thought": "Now I will apply the tested changes.",
                "tool_calls": [{"id": "call_2", "name": "write_file", "arguments": "{\"path\": \"test.py\"}"}],
                "timestamp": "12:00:03",
            },
        ),
        # Tool output 2
        ContextMessage(
            role="tool",
            content="File written successfully",
            zone="zone2_linear",
            tool_call_id="call_2",
            metadata={"tool_name": "write_file", "is_error": False},
        ),
        # Final step in turn: Final response text
        ContextMessage(
            role="assistant",
            content="All changes applied according to knowledge base.",
            zone="zone2_linear",
            metadata={"thought": "Done verifying output.", "timestamp": "12:00:05"},
        ),
    ]

    chat_msgs = context_messages_to_chat_messages(msgs)

    # 1. Grouped by TURN: Exactly 1 user message and 1 assistant message
    assert len(chat_msgs) == 2
    assert chat_msgs[0]["role"] == "user"
    assert chat_msgs[0]["content"] == "Search knowledge and edit file"

    asst_msg = chat_msgs[1]
    assert asst_msg["role"] == "assistant"
    assert asst_msg["content"] == "All changes applied according to knowledge base."

    # 2. Sequential items: ordered chronologically
    items = asst_msg.get("items", [])
    item_types = [item["type"] for item in items]
    assert item_types == [
        "reasoning",        # Step 1 thought
        "tool_call",        # Step 1 call_1
        "knowledge_agent",  # Step 1 knowledge event 1
        "knowledge_agent",  # Step 1 knowledge event 2
        "reasoning",        # Step 2 thought
        "tool_call",        # Step 2 call_2
        "reasoning",        # Final thought
    ]

    # Verify tool results populated on tool_calls
    assert items[1]["id"] == "call_1"
    assert items[1]["result"] == "Found knowledge: testing patterns"
    assert items[5]["id"] == "call_2"
    assert items[5]["result"] == "File written successfully"

    # Verify knowledge events preserved
    assert len(asst_msg.get("knowledgeEvents", [])) == 2
    assert asst_msg["knowledgeEvents"][0]["agent"] == "CuratorAgent"
    assert asst_msg["knowledgeEvents"][1]["agent"] == "CheckerAgent"

    # Verify reasoning thoughts preserved
    assert "I should first consult the knowledge base." in asst_msg.get("thought", "")
    assert "Now I will apply the tested changes." in asst_msg.get("thought", "")

