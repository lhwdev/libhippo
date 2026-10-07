"""Tests for ruamel.yaml migration, comment preservation, related path validation, and tag regulation."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from libhippo.agents.checker import CheckerAgent
from libhippo.agents.draftsman import KnowledgeDraftSession
from libhippo.models.knowledge import (
    KnowledgeCandidate,
    KnowledgeFrontmatter,
    normalize_tags,
)
from libhippo.storage.store import KnowledgeStore


def test_ruamel_comment_preservation():
    """Verify that comments before and after frontmatter keys are preserved on round-trip."""
    md_with_comments = """---
# Documentation top comment
title: "React Components" # inline title comment
# Source section comment
source:
  - "https://react.dev"
tags: ["react", "ui"]
related: []
---

## Summary
Pure components.
"""
    candidate = KnowledgeCandidate.from_markdown("common/web/react.md", md_with_comments)
    assert candidate.frontmatter is not None
    assert candidate.frontmatter.title == "React Components"

    # Modify title and re-serialize
    candidate.frontmatter.title = "React 19 Components"
    output = candidate.to_markdown()

    assert "# Documentation top comment" in output
    assert "# Source section comment" in output
    assert 'title: "React 19 Components"' in output or "title: React 19 Components" in output
    assert 'tags: ["react", "ui"]' in output


def test_ruamel_formatting_inline_tags_and_block_source():
    """Verify tags format as inline flow array and source/related format as block or [] if empty."""
    cand = KnowledgeCandidate(
        path="common/test",
        markdown="",
        frontmatter=KnowledgeFrontmatter(
            title="Test Document",
            tags=["javascript", "typescript", "frontend"],
            source=["https://example.com/docs/1", "https://example.com/docs/2"],
            related=[],
        ),
        body="Body text here.",
    )
    md = cand.to_markdown()
    assert 'tags: ["javascript", "typescript", "frontend"]' in md
    assert "source:\n  - https://example.com/docs/1\n  - https://example.com/docs/2" in md or (
        "source:\n  - \"https://example.com/docs/1\"\n  - \"https://example.com/docs/2\"" in md
    )
    assert "related: []" in md


def test_tag_normalization_and_capping():
    """Verify tag lowercasing, deduplication, and capping at MAX_TAGS = 8."""
    raw_tags = [
        "React",
        "react",
        "REACT",
        "UI",
        "Frontend",
        "Hooks",
        "State",
        "Props",
        "JSX",
        "DOM",
        "Fiber",
        "Virtual DOM",
    ]
    normalized = normalize_tags(raw_tags)
    assert len(normalized) == 8
    assert normalized[0] == "react"
    assert normalized[1] == "ui"
    assert "react" in normalized
    assert normalized.count("react") == 1


@pytest.mark.asyncio
async def test_write_knowledge_warns_on_missing_related(tmp_path: Path):
    """Verify write_knowledge produces non-blocking warnings when referencing non-existent documents."""
    session = KnowledgeDraftSession(workspace_root=tmp_path)
    try:
        md = """---
title: "React Hook Form"
tags: ["react", "forms"]
related:
  - "common/web/missing_doc"
---
## Summary
Forms guide.
"""
        out = await session.write_knowledge("common/web/forms", md)
        assert "Warnings:" in out
        assert "common/web/missing_doc" in out
        assert "does not exist in knowledge store or active drafts" in out
        assert "Status: REQUIRES_REVISION" not in out
    finally:
        session.cleanup()


@pytest.mark.asyncio
async def test_commit_rejects_missing_related(tmp_path: Path):
    """Verify commit returns error when related document does not exist."""
    session = KnowledgeDraftSession(workspace_root=tmp_path)
    try:
        md = """---
title: "React Hook Form"
tags: ["react", "forms"]
related:
  - "common/web/nonexistent_lib"
---
## Summary
Forms guide.
"""
        await session.write_knowledge("common/web/forms", md)
        res = await session.commit("common/web/forms")
        assert res["status"] == "error"
        assert "failed validation: related document(s)" in res["message"]
        assert any("nonexistent_lib" in err for err in res["errors"])
    finally:
        session.cleanup()


@pytest.mark.asyncio
async def test_commit_allows_existing_and_empty_related(tmp_path: Path):
    """Verify commit succeeds when related documents exist in session or related is empty."""
    session = KnowledgeDraftSession(workspace_root=tmp_path)
    try:
        # Doc 1: empty related
        md1 = """---
title: "Doc One"
tags: ["one"]
related: []
---
## Summary
Doc one summary.
"""
        await session.write_knowledge("common/doc1", md1)
        res1 = await session.commit("common/doc1")
        assert res1["status"] == "ready"

        # Doc 2: references doc 1 (which is now in drafts/session)
        # Re-add doc1 as a draft to simulate multi-draft existence
        await session.write_knowledge("common/doc1", md1)
        md2 = """---
title: "Doc Two"
tags: ["two"]
related:
  - "common/doc1"
---
## Summary
Doc two summary.
"""
        await session.write_knowledge("common/doc2", md2)
        res2 = await session.commit("common/doc2")
        assert res2["status"] == "ready"
    finally:
        session.cleanup()


@pytest.mark.asyncio
async def test_bookkeeper_restricted_tag_removal(tmp_path: Path):
    """Verify improve_search_confidence removes up to 2 tags with restrictions and caps additions at 10."""
    store = KnowledgeStore(root_dir=tmp_path / "knowledge")
    await store.initialize()

    node_md = """---
title: "Component State"
tags: ["react", "obsolete_tag", "redundant_tag", "state"]
related: []
---
## Summary
State management.
"""
    await store.save_node("common/web/state", node_md, sync_index=False)

    # 1. Prune 2 tags and add new keyword
    success = await store.improve_search_confidence(
        "common/web/state",
        keywords=["reducer"],
        remove_tags=["obsolete_tag", "redundant_tag"],
    )
    assert success is True

    node = await store.get_node("common/web/state")
    assert node is not None
    assert "obsolete_tag" not in node.frontmatter.tags
    assert "redundant_tag" not in node.frontmatter.tags
    assert "reducer" in node.frontmatter.tags
    assert "react" in node.frontmatter.tags
    assert "state" in node.frontmatter.tags

    # 2. Cannot wipe out all tags: if only 1 tag remains, removal is halted
    node.frontmatter.tags = ["sole_tag"]
    await store.save_node("common/web/state", node.to_markdown(), sync_index=False)
    await store.improve_search_confidence(
        "common/web/state",
        keywords=[],
        remove_tags=["sole_tag"],
    )
    updated = await store.get_node("common/web/state")
    assert updated is not None
    assert len(updated.frontmatter.tags) == 1
    assert "sole_tag" in updated.frontmatter.tags


@pytest.mark.asyncio
async def test_checker_agent_tag_bloat_filter():
    """Verify CheckerAgent detects tag bloat and rejects with REVISE_CONTENT."""
    mock_client = MagicMock()
    mock_client.system_one = MagicMock()

    # Configure mock response with high tag bloat probability
    mock_response = MagicMock(
        choices={"taxonomy_fit": MagicMock(choice="optimal")},
        scores={
            "bloatedness": MagicMock(score=1.0),
            "importance": MagicMock(score=1.8),
            "rule_effectiveness": MagicMock(score=1.0),
            "coherence": MagicMock(score=2.0),
            "grammar_and_clarity": MagicMock(score=2.0),
            "markdown_format_quality": MagicMock(score=2.0),
            "practical_utility": MagicMock(score=2.0),
            "tag_quality": MagicMock(score=0.2),  # low quality
        },
        nouls={
            "is_coalescence_candidate": MagicMock(noul=0.1),
            "has_redundancy_or_conflict": MagicMock(noul=0.05),
            "has_tag_bloat_or_generic_noise": MagicMock(noul=0.85),  # high bloat
        },
    )

    async def fake_system_one(*args, **kwargs):
        return mock_response

    mock_client.system_one = fake_system_one

    agent = CheckerAgent(client=mock_client)
    candidate = KnowledgeCandidate.from_markdown(
        "common/web/react",
        """---
title: "React"
tags: ["react", "js", "web", "code", "lib", "front"]
related: []
---
## Summary
React overview.
## Detailed Rules
Use functional components with hooks.
""",
    )

    report = await agent.check(candidate)
    assert report.verdict == "REVISE_CONTENT"
    assert any("Frontmatter tags contain excessive bloat" in err for err in report.content_errors)
