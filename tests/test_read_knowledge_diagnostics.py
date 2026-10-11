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
        assert res["total_lines"] == 4
        assert res["content"] == "# React Guide\nLine 1: Components"

        # String representation uses unnumbered <knowledge ...> XML format with essential frontmatter
        res_str = str(res)
        assert '<knowledge path="common/react" lines="1:2" total_lines="4">' in res_str
        assert "# React Guide\nLine 1: Components\n</knowledge>" in res_str
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_knowledge_tools_read_knowledge_essential_frontmatter(tmp_path: Path):
    store = await _create_test_store(tmp_path)
    common_dir = tmp_path / "common"
    vue_file = common_dir / "vue.md"
    vue_file.write_text(
        "---\ntitle: Vue Framework\nnamespace: common\nversion: 2.1.0\nlast_updated: '2026-10-10'\nrelated:\n  - common/react\nauthor: Antigravity\n---\n# Vue\nReactivity core\n",
        encoding="utf-8",
    )
    try:
        tools = KnowledgeTools(store=store)
        res = await tools.read_knowledge("common/vue")
        assert res["status"] == "success"
        content = res["content"]
        assert "version: 2.1.0" in content
        assert "last_updated: '2026-10-10'" in content
        assert "related:\n- common/react" in content or "related:\n  - common/react" in content
        # Non-essential keys must be omitted
        assert "namespace:" not in content
        assert "author:" not in content
        # Title merged into H1 (longer wins)
        assert "# Vue Framework" in content
        assert "# Vue\n" not in content
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_draftsman_read_knowledge_raw_with_line_numbers(tmp_path: Path):
    store = await _create_test_store(tmp_path)
    try:
        session = KnowledgeDraftSession(store=store, workspace_root=tmp_path)
        res = await session.read_knowledge("common/react", start_line=1, end_line=3)
        assert '<knowledge path="common/react" lines="1:3" total_lines="8">' in res
        assert "1: ---" in res
        assert "2: title: React Guide" in res
        assert "3: namespace: common" in res
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_query_knowledge_xml_output_format(tmp_path: Path):
    from unittest.mock import AsyncMock
    from libhippo.tools.retrieval import KnowledgeRetrievalResult

    store = await _create_test_store(tmp_path)
    dispatcher = AsyncMock()
    dispatcher.query_knowledge.return_value = KnowledgeRetrievalResult(
        status="HIT",
        path="common/react",
        title="React Guide",
        content="---\ntitle: React Guide\nnamespace: common\n---\n# React\nLine 1: Components",
        confidence=0.95,
        effort_tier="medium",
        criticality="preferred",
        source="fast_path",
    )
    try:
        tools = KnowledgeTools(store=store, dispatcher=dispatcher)
        res = await tools.query_knowledge(query="react components")
        assert res["status"] == "HIT"
        res_str = str(res)
        assert '<query_knowledge.hit query="react components" effort="medium" criticality="preferred" confidence="0.95">' in res_str
        assert '<knowledge path="common/react" total_lines="2">' in res_str
        assert "# React Guide\nLine 1: Components" in res_str
        assert "</knowledge>" in res_str
        assert "</query_knowledge.hit>" in res_str
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
