"""Tests for ResourceDiscovery and ConversationSession."""

import asyncio
from pathlib import Path
import pytest

from libhippo.runner.config import HarnessConfig
from libhippo.runner.discovery import ResourceDiscovery
from libhippo.runner.persistence import ConversationSession
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


@pytest.mark.asyncio
async def test_conversation_persistence(tmp_path: Path):
    """Test ConversationSession persists messages, artifacts, and tasks."""
    storage = tmp_path / "session_storage"
    session = ConversationSession(
        project_id="test-proj",
        conversation_id="conv-123",
        storage_dir=storage,
    )

    # 1. Append message
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

    # 2. Record artifact
    art_path = await session.record_artifact(
        name="plan.md",
        content="# Plan\n1. Do X",
        metadata={"Summary": "Test plan", "UserFacing": True},
    )
    assert art_path.exists()
    artifacts = session.list_artifacts()
    assert len(artifacts) == 1
    assert artifacts[0]["name"] == "plan.md"
    assert artifacts[0]["metadata"]["UserFacing"] is True

    # 3. Record task and subagent
    await session.record_task("task-1", {"status": "completed", "exit_code": 0})
    await session.record_subagent("sub-1", {"role": "reviewer", "state": "idle"})

    subagents = session.list_subagents()
    assert len(subagents) == 1
    assert subagents[0]["role"] == "reviewer"
