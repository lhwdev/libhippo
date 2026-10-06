"""Tests for read_knowledge error diagnostics and KnowledgeTools integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from libhippo.agents.draftsman import KnowledgeDraftSession
from libhippo.runner.tools.knowledge_tools import KnowledgeTools
from libhippo.storage.mount import MountConfig, MountManager
from libhippo.storage.store import KnowledgeStore


async def _create_test_store(tmp_path: Path) -> KnowledgeStore:
    project_dir = tmp_path / "project"
    common_dir = tmp_path / "common"
    project_dir.mkdir(parents=True, exist_ok=True)
    common_dir.mkdir(parents=True, exist_ok=True)

    # Seed files
    react_file = common_dir / "react.md"
    react_file.write_text(
        "---\ntitle: React Guide\nnamespace: common\n---\n# React\nLine 1: Components\nLine 2: Hooks\nLine 3: Server Actions\n",
        encoding="utf-8",
    )

    manager = MountManager(
        mounts=[
            MountConfig(namespace_prefix="project", physical_path=project_dir, read_only=False),
            MountConfig(namespace_prefix="common", physical_path=common_dir, read_only=False),
        ],
        fallback_root=tmp_path,
    )
    store = KnowledgeStore(
        root_dir=tmp_path,
        cache_dir=tmp_path / ".cache",
        mounts=manager,
    )
    await store.initialize()
    return store


@pytest.mark.asyncio
async def test_read_knowledge_no_active_draft_suggests_existing_sample(tmp_path: Path):
    store = await _create_test_store(tmp_path)
    try:
        session = KnowledgeDraftSession(store=store, workspace_root=tmp_path)
        # Empty or missing path specified
        res = await session.read_knowledge("")
        assert "ERROR: No knowledge path specified." in res
        assert "common/react" in res
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_read_knowledge_fuzzy_match_suggestion(tmp_path: Path):
    store = await _create_test_store(tmp_path)
    try:
        session = KnowledgeDraftSession(store=store, workspace_root=tmp_path)
        # Misspelled path
        res = await session.read_knowledge("common/reakt")
        assert "ERROR: Knowledge path 'common/reakt' not found" in res
        assert "Did you mean 'common/react'?" in res
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_knowledge_tools_read_knowledge_success(tmp_path: Path):
    store = await _create_test_store(tmp_path)
    try:
        tools = KnowledgeTools(store=store)
        res = await tools.read_knowledge("common/react", start_line=1, end_line=2)
        assert res["status"] == "success"
        assert res["path"] == "common/react"
        assert res["start_line"] == 1
        assert res["end_line"] == 2
        assert "1: ---" in res["content"]
        assert "2: title: React Guide" in res["content"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_knowledge_tools_read_knowledge_not_found_with_suggestion(tmp_path: Path):
    store = await _create_test_store(tmp_path)
    try:
        tools = KnowledgeTools(store=store)
        res = await tools.read_knowledge("common/reakt")
        assert res["status"] == "not_found"
        assert "Did you mean 'common/react'?" in res["message"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_knowledge_tools_tool_definitions_include_read_knowledge(tmp_path: Path):
    store = await _create_test_store(tmp_path)
    try:
        tools = KnowledgeTools(store=store)
        defs = tools.get_tool_definitions()
        assert "read_knowledge" in defs
        tool_def = defs["read_knowledge"]
        assert tool_def.name == "read_knowledge"
        assert "path" in tool_def.parameters_schema["required"]
    finally:
        await store.close()
