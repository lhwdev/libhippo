"""Unit tests for automated freshness verification, staleness routing, and bi-directional cascading."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from libhippo.models.knowledge import (
    HubReference,
    KnowledgeCandidate,
    KnowledgeContext,
    KnowledgeFrontmatter,
    normalize_frontmatter,
    update_markdown_version,
)
from libhippo.storage.catalog import KnowledgeCatalog
from libhippo.storage.freshness import FreshnessChecker
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.retrieval import KnowledgeDispatcher


def test_frontmatter_source_and_normalization():
    """Verify source and version_check fields and silent normalization auto-fixes."""
    md = """---
title: "React Hooks"
namespace: "common"
version: "19.0.0"
source: "https://react.dev"
version_check: "npm:react"
tags: "react, hooks, state"
---
## Summary
Use hooks.
## Detailed Rules
Do not call hooks conditionally.
"""
    candidate = KnowledgeCandidate.from_markdown("common/web/react/hooks.md", md)
    assert candidate.frontmatter is not None
    # Verify auto-coercion of scalar strings to lists
    assert candidate.frontmatter.source == ["https://react.dev"]
    assert candidate.frontmatter.tags == ["react", "hooks", "state"]

    # Verify silent drop of child redundant version_check
    norm_candidate, fixes = normalize_frontmatter(
        candidate,
        parent_version_check="npm:react",
        is_category_hub=False,
    )
    assert norm_candidate.frontmatter.version_check is None
    assert len(fixes) == 1
    assert "Silently dropped redundant child version_check" in fixes[0]
    assert "version_check" not in norm_candidate.markdown


def test_category_hub_normalization():
    """Verify version_check is silently stripped from category hubs."""
    md = """---
title: "Web Standards"
namespace: "common"
version: "1.0.0"
version_check: "npm:web"
---
## Summary
Web overview.
"""
    candidate = KnowledgeCandidate.from_markdown("common/web.md", md)
    norm_candidate, fixes = normalize_frontmatter(
        candidate,
        parent_version_check=None,
        is_category_hub=True,
    )
    assert norm_candidate.frontmatter.version_check is None
    assert len(fixes) == 1
    assert "category hub" in fixes[0]


def test_semver_comparison():
    """Verify SemVer comparison distinguishes stale, major, minor, and fresh."""
    checker = FreshnessChecker()

    # Major bump
    stale, major, minor = checker.compare_versions("18.2.0", "19.0.0")
    assert stale is True
    assert major is True
    assert minor is False

    # Minor bump
    stale, major, minor = checker.compare_versions("19.0.0", "19.1.0")
    assert stale is True
    assert major is False
    assert minor is True

    # Same version
    stale, major, minor = checker.compare_versions("19.0.0", "19.0.0")
    assert stale is False

    # Older upstream
    stale, major, minor = checker.compare_versions("19.1.0", "19.0.0")
    assert stale is False


@pytest.mark.asyncio
async def test_infer_check_spec():
    """Verify version_check parsing and fallback inference from sources."""
    checker = FreshnessChecker()

    assert checker.infer_check_spec("npm:react", []) == ("npm", "react")
    assert checker.infer_check_spec("pypi:fastapi", []) == ("pypi", "fastapi")
    assert checker.infer_check_spec("github:tiangolo/fastapi", []) == ("github", "tiangolo/fastapi")
    assert checker.infer_check_spec("crates:tokio", []) == ("crates", "tokio")
    assert checker.infer_check_spec("terminal:python --version", []) == ("terminal", "python --version")

    # Inferred from sources
    assert checker.infer_check_spec(None, ["https://github.com/facebook/react"]) == ("github", "facebook/react")
    assert checker.infer_check_spec(None, ["https://pypi.org/project/pydantic/"]) == ("pypi", "pydantic")
    assert checker.infer_check_spec(None, ["https://www.npmjs.com/package/next"]) == ("npm", "next")


@pytest.mark.asyncio
async def test_catalog_freshness_and_bidirectional_cascading(tmp_path):
    """Verify SQLite catalog columns, update_freshness, and child cascading."""
    db_path = tmp_path / "catalog.db"
    async with KnowledgeCatalog(db_path) as catalog:
        await catalog.initialize()

        # Insert parent hub
        parent_candidate = KnowledgeCandidate.from_markdown(
            "common/web/react.md",
            """---
title: "React Hub"
namespace: "common"
version: "19.0.0"
version_check: "npm:react"
source: ["https://react.dev"]
---
## Summary
React Hub overview.
""",
        )
        await catalog.upsert(parent_candidate)

        # Insert child leaf
        child_candidate = KnowledgeCandidate.from_markdown(
            "common/web/react/hooks.md",
            """---
title: "React Hooks"
namespace: "common"
version: "19.0.0"
---
## Summary
Hooks rules.
""",
        )
        await catalog.upsert(child_candidate)

        # 1. Update parent to stale -> children should cascade to stale
        await catalog.update_freshness(
            path="common/web/react.md",
            freshness_status="stale",
            upstream_version="19.1.0",
            stale_reason="version_bump:19.0.0->19.1.0",
            cascade_children=True,
        )

        parent_entry = await catalog.get("common/web/react.md")
        assert parent_entry["freshness_status"] == "stale"
        assert parent_entry["upstream_version"] == "19.1.0"

        child_entry = await catalog.get("common/web/react/hooks.md")
        assert child_entry["freshness_status"] == "stale"
        assert child_entry["stale_reason"] == "parent_stale:common/web/react.md"

        # 2. Update parent to fresh -> children should clear stale
        await catalog.update_freshness(
            path="common/web/react.md",
            freshness_status="fresh",
            upstream_version="19.1.0",
            stale_reason=None,
            cascade_children=True,
        )

        child_entry_cleared = await catalog.get("common/web/react/hooks.md")
        assert child_entry_cleared["freshness_status"] == "fresh"
        assert child_entry_cleared["stale_reason"] is None


@pytest.mark.asyncio
async def test_read_section_metadata_banner(tmp_path):
    """Verify read_section prepends metadata visibility banner for web-fetched knowledge."""
    store = KnowledgeStore(root_dir=tmp_path)
    await store.initialize()

    await store.save_node(
        "common/web/react.md",
        """---
title: "React Core"
namespace: "common"
version: "19.0.0"
source:
  - "https://react.dev"
version_check: "npm:react"
---
## Summary
React component primitives.
## Detailed Rules
Never mutate state directly.
""",
        resolve_version=False,
    )

    content = await store.read_section("common/web/react.md", section="summary")
    assert content is not None
    assert "[Knowledge Metadata | version: 19.0.0" in content
    assert "source: https://react.dev" in content
    assert "React component primitives." in content
    await store.close()


@pytest.mark.asyncio
async def test_modify_knowledge_revalidate(tmp_path):
    """Verify modify_knowledge(action='revalidate') performs forced freshness check."""
    store = KnowledgeStore(root_dir=tmp_path)
    await store.initialize()

    await store.save_node(
        "common/web/react.md",
        """---
title: "React Core"
namespace: "common"
version: "19.0.0"
source:
  - "https://react.dev"
version_check: "npm:react"
---
## Summary
React component primitives.
""",
        resolve_version=False,
    )

    with patch.object(FreshnessChecker, "fetch_npm_version", new=AsyncMock(return_value="19.1.0")):
        res = await store.modify_knowledge("revalidate", "common/web/react.md")
        assert res["status"] == "success"
        assert res["action"] == "revalidate"
        assert res["upstream_version"] == "19.1.0"
        assert res["revalidation"] == "stale"

        # Catalog should reflect stale status
        entry = await store.catalog.get("common/web/react.md")
        assert entry["freshness_status"] == "stale"

    await store.close()


@pytest.mark.asyncio
async def test_query_knowledge_staleness_routing(tmp_path):
    """Verify 3-tier staleness routing in query_knowledge: Mode A, Mode B, Mode C."""
    store = KnowledgeStore(root_dir=tmp_path)
    await store.initialize()

    await store.save_node(
        "common/web/react.md",
        """---
title: "React Core"
namespace: "common"
version: "19.0.0"
source: ["https://react.dev"]
version_check: "npm:react"
---
## Summary
React core library.
## Detailed Rules
Hooks rules.
""",
    )

    # Mark node stale in catalog
    await store.catalog.update_freshness(
        "common/web/react.md",
        freshness_status="stale",
        upstream_version="19.1.0",
        stale_reason="version_bump:19.0.0->19.1.0",
    )

    dispatcher = KnowledgeDispatcher(
        store=store,
        threshold_low=0.50,
        threshold_medium=0.50,
    )

    # Mode A: No fetch (optional criticality or low effort)
    res_a = await dispatcher.query_knowledge(
        query="React Core",
        effort="low",
        criticality="optional",
    )
    assert res_a.status == "HIT"
    assert "This knowledge is outdated" in res_a.content
    assert "Query with higher criticality/effort" in res_a.content

    # Mode B: Stale-While-Revalidate (preferred criticality, medium effort)
    res_b = await dispatcher.query_knowledge(
        query="React Core",
        effort="medium",
        criticality="preferred",
    )
    assert res_b.status == "HIT"
    assert "Background revalidation queued" in res_b.content

    # Mode C: Wait for latest (mandatory criticality, high effort)
    with patch.object(FreshnessChecker, "fetch_npm_version", new=AsyncMock(return_value="19.1.0")):
        res_c = await dispatcher.query_knowledge(
            query="React Core",
            effort="high",
            criticality="mandatory",
        )
        assert res_c.status == "HIT"

    await store.close()


def test_update_markdown_version():
    """Verify update_markdown_version correctly replaces or injects version."""
    # 1. Quoted version
    raw = """---
title: "React"
version: "19.0.0"
version_check: "npm:react"
---
## Summary
React.
"""
    updated = update_markdown_version(raw, "19.2.0")
    assert 'version: "19.2.0"' in updated
    assert 'version: "19.0.0"' not in updated
    assert 'title: "React"' in updated

    # 2. Unquoted version
    raw_unquoted = """---
title: "FastAPI"
version: 0.115.0
version_check: "pypi:fastapi"
---
"""
    updated_unquoted = update_markdown_version(raw_unquoted, "0.116.0")
    assert 'version: "0.116.0"' in updated_unquoted

    # 3. Missing version field (injected)
    raw_missing = """---
title: "Tokio"
version_check: "crates:tokio"
---
Body
"""
    updated_missing = update_markdown_version(raw_missing, "1.40.0")
    assert 'version: "1.40.0"' in updated_missing
    assert "Body" in updated_missing


@pytest.mark.asyncio
async def test_save_node_overwrites_version_with_upstream(tmp_path):
    """Verify save_node fetches upstream version from version_check and overwrites frontmatter version."""
    store = KnowledgeStore(root_dir=tmp_path)
    await store.initialize()

    raw_doc = """---
title: "React Core"
namespace: "common"
version: "19.0.0"
source: ["https://react.dev"]
version_check: "npm:react"
---
## Summary
React core library.
"""
    with patch.object(FreshnessChecker, "fetch_npm_version", new=AsyncMock(return_value="19.2.0")):
        candidate = await store.save_node("common/web/react.md", raw_doc)

        # Candidate should have overwritten version
        assert candidate.frontmatter.version == "19.2.0"

        # File written on disk should have overwritten version
        fs_path, _ = store.mount_manager.resolve_virtual_path("common/web/react.md")
        disk_content = fs_path.read_text(encoding="utf-8")
        assert 'version: "19.2.0"' in disk_content
        assert 'version: "19.0.0"' not in disk_content

        # Catalog should reflect new version and fresh status
        entry = await store.catalog.get("common/web/react.md")
        assert entry["version"] == "19.2.0"
        assert entry["freshness_status"] == "fresh"
        assert entry["upstream_version"] == "19.2.0"

    await store.close()


@pytest.mark.asyncio
async def test_modify_knowledge_overwrites_version_with_upstream_url(tmp_path):
    """Verify modify_knowledge tool resolves direct URL or package spec to overwrite version."""
    store = KnowledgeStore(root_dir=tmp_path)
    await store.initialize()

    raw_doc = """---
title: "FastAPI"
namespace: "common"
version: "0.100.0"
version_check: "https://pypi.org/project/fastapi"
---
## Summary
FastAPI framework.
"""
    with patch.object(FreshnessChecker, "fetch_pypi_version", new=AsyncMock(return_value="0.115.0")):
        res = await store.modify_knowledge(
            action="create",
            path="common/python/fastapi.md",
            content=raw_doc,
        )
        assert res["status"] == "success"
        assert res["version"] == "0.115.0"

        # Verify disk
        node = await store.get_node("common/python/fastapi.md")
        assert node is not None
        assert node.frontmatter.version == "0.115.0"
        assert 'version: "0.115.0"' in node.markdown

    await store.close()


@pytest.mark.asyncio
async def test_save_node_graceful_on_network_failure(tmp_path):
    """Verify save_node preserves original version if upstream fetch fails/returns None."""
    store = KnowledgeStore(root_dir=tmp_path)
    await store.initialize()

    raw_doc = """---
title: "React Core"
namespace: "common"
version: "19.0.0"
version_check: "npm:react"
---
## Summary
React core library.
"""
    with patch.object(FreshnessChecker, "fetch_npm_version", new=AsyncMock(return_value=None)):
        candidate = await store.save_node("common/web/react.md", raw_doc)
        assert candidate.frontmatter.version == "19.0.0"

        entry = await store.catalog.get("common/web/react.md")
        assert entry["version"] == "19.0.0"

    await store.close()
