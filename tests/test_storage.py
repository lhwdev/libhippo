"""Unit tests for KnowledgeStorage, KnowledgeCatalog, and VectorKnowledgeStore."""

import pytest

from libhippo.models.knowledge import KnowledgeCandidate
from libhippo.storage.catalog import KnowledgeCatalog
from libhippo.storage.store import KnowledgeStore, extract_sections
from libhippo.storage.vector import VectorKnowledgeStore


def test_extract_sections():
    """Verify regex extraction of coarse summary and fine detailed rules."""
    md = """---
title: "Sample"
namespace: "common"
---

## Summary (Coarse View)
This is the coarse summary of the rule.

## Detailed Rules & Edge Cases (Fine View)
- Rule item 1
- Rule item 2
"""
    summary, rules = extract_sections(md)
    assert summary == "This is the coarse summary of the rule."
    assert "Rule item 1" in rules
    assert "Rule item 2" in rules


@pytest.mark.asyncio
async def test_knowledge_catalog(tmp_path):
    """Test SQLite catalog CRUD, FTS search, and access recording."""
    db_path = tmp_path / "catalog.db"
    async with KnowledgeCatalog(db_path) as catalog:
        await catalog.initialize()

        candidate = KnowledgeCandidate.from_markdown(
            "common/web/html/button.md",
            """---
title: "Button Rules"
namespace: "common"
importance: 0.85
tags: ["html", "button"]
related: ["common/web/html.md"]
---
## Summary
Use button tags.
## Detailed Rules
Do not use div.
""",
        )

        await catalog.upsert(candidate, summary="Use button tags.", rules="Do not use div.")

        # Fetch
        entry = await catalog.get("common/web/html/button.md")
        assert entry is not None
        assert entry["title"] == "Button Rules"
        assert entry["importance"] == 0.85
        assert "button" in entry["tags"]

        # FTS search
        results = await catalog.search_fts("button")
        assert len(results) >= 1
        assert results[0]["path"] == "common/web/html/button.md"

        # Record access
        await catalog.record_access("common/web/html/button.md")
        updated = await catalog.get("common/web/html/button.md")
        assert updated["access_count"] == 1
        assert updated["last_accessed"] is not None

        # Delete
        await catalog.delete("common/web/html/button.md")
        assert await catalog.get("common/web/html/button.md") is None


def test_vector_knowledge_store(tmp_path):
    """Test ChromaDB vector store chunk indexing and confidence scoring."""
    store = VectorKnowledgeStore(persist_dir=tmp_path / "chroma")
    candidate = KnowledgeCandidate.from_markdown(
        "common/web/html/accessibility/aria_button.md",
        """---
title: "Button Accessibility with ARIA"
namespace: "common"
importance: 0.85
---
## Summary
Use native button elements for full keyboard accessibility.
## Detailed Rules
Ensure preventDefault on Space key.
""",
    )

    store.upsert(
        candidate,
        summary="Use native button elements for full keyboard accessibility.",
        rules="Ensure preventDefault on Space key.",
    )

    results = store.search("keyboard accessibility", top_k=2)
    assert len(results) >= 1
    top = results[0]
    assert top.path == "common/web/html/accessibility/aria_button.md"
    assert top.importance == 0.85
    # Verify importance confidence formula: (1 - 0.08) * sim + 0.08 * 0.85
    expected_conf = round(0.92 * top.cosine_sim + 0.08 * 0.85, 4)
    assert top.confidence == expected_conf


@pytest.mark.asyncio
async def test_knowledge_store_facade(tmp_path):
    """Test KnowledgeStore file coordination, read_section, and modify_knowledge."""
    async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
        doc_content = """---
title: "React 19 Action Hooks"
namespace: "plugins"
importance: 0.90
---

## Summary (Coarse View)
In React 19, useActionState replaces useFormState.

## Detailed Rules & Edge Cases (Fine View)
- [state, formAction, isPending] = useActionState(fn, initialState)
- Handles async transitions automatically.
"""
        # 1. Create via modify_knowledge
        res = await store.modify_knowledge(
            action="create",
            path="plugins/react19/actions.md",
            content=doc_content,
        )
        assert res["status"] == "success"

        # 2. Read sections
        summary = await store.read_section("plugins/react19/actions.md", section="summary")
        assert "useActionState replaces useFormState" in summary

        rules = await store.read_section("plugins/react19/actions.md", section="rules")
        assert "useActionState(fn, initialState)" in rules

        # 3. Search
        search_res = await store.search("useActionState formAction")
        assert len(search_res) >= 1
        assert search_res[0].path == "plugins/react19/actions.md"

        # 4. Merge action
        res_merge = await store.modify_knowledge(
            action="merge",
            path="plugins/react19/actions.md",
            content=doc_content + "\n- Consolidated extra rules",
            extra_paths=["plugins/react19/old_actions.md"],
        )
        assert res_merge["status"] == "success"

        # 5. Purge / Deprecate
        res_purge = await store.modify_knowledge(
            action="purge",
            path="plugins/react19/actions.md",
            metadata={"reason": "Superceded"},
        )
        assert res_purge["status"] == "success"
        assert "deprecated" in res_purge["quarantined_to"]


@pytest.mark.asyncio
async def test_knowledge_store_force_keep(tmp_path):
    """Verify force_keep blocks rename/merge/purge unless explicitly overridden."""
    async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
        doc = """---
title: "Pinned Web Rule"
namespace: "common"
importance: 0.90
force_keep: true
---
## Summary
Important pinned rule.
## Detailed Rules
Do not delete or rename.
"""
        await store.save_node("common/web/pinned.md", doc)

        # Attempt to purge without force=True -> raises ValueError
        with pytest.raises(ValueError, match="Cannot deprecate node 'common/web/pinned.md' with force_keep=True"):
            await store.modify_knowledge(action="purge", path="common/web/pinned.md")

        # Attempt to merge into another doc without force=True -> raises ValueError
        with pytest.raises(ValueError, match="Cannot coalesce node 'common/web/pinned.md' with force_keep=True"):
            await store.modify_knowledge(
                action="merge",
                path="common/web/merged.md",
                content="New content",
                extra_paths=["common/web/pinned.md"],
            )


        # Purge succeeds when force=True
        res = await store.modify_knowledge(
            action="purge",
            path="common/web/pinned.md",
            force=True,
            metadata={"reason": "Forced cleanup"},
        )
        assert res["status"] == "success"


@pytest.mark.asyncio
async def test_knowledge_store_incremental_sync(tmp_path):
    """Verify 3-tier sync: mtime -> SHA256 -> selective upsert + orphan deletion."""
    root = tmp_path / "knowledge"
    async with KnowledgeStore(root_dir=root) as store:
        doc1 = """---
title: "File 1"
namespace: "common"
---
## Summary
Initial summary.
## Detailed Rules
Initial rules.
"""
        await store.save_node("common/file1.md", doc1)

        # 1. First sync: everything up-to-date
        report1 = await store.sync_all()
        assert report1["upserted"] == 0
        assert report1["deleted"] == 0
        assert report1["mtime_only_updated"] == 0

        # 2. External edit directly on disk (simulating git pull or manual edit)
        file1_path = root / "common" / "file1.md"
        doc1_updated = doc1.replace("Initial summary.", "Updated summary.")
        file1_path.write_text(doc1_updated, encoding="utf-8")

        report2 = await store.sync_all()
        assert report2["upserted"] == 1
        assert "common/file1.md" in report2["details"]["upserted"]
        cat_entry = await store.catalog.get("common/file1.md")
        assert cat_entry is not None
        summary = await store.read_section("common/file1.md", "summary")
        assert "Updated summary." in summary


        # 3. Touch file without changing content (same SHA) -> mtime_only_updated
        import os
        import time
        new_time = time.time() + 10
        os.utime(file1_path, (new_time, new_time))

        report3 = await store.sync_all()
        assert report3["upserted"] == 0
        assert report3["mtime_only_updated"] == 1

        # 4. Delete file on disk (orphan detection)
        file1_path.unlink()
        report4 = await store.sync_all()
        assert report4["deleted"] == 1
        assert "common/file1.md" in report4["details"]["deleted"]
        assert await store.catalog.get("common/file1.md") is None


@pytest.mark.asyncio
async def test_knowledge_store_post_task_maintenance(tmp_path):
    """Verify post_task_maintenance triggers rebuild when threshold is met and resets mutation count."""
    async with KnowledgeStore(root_dir=tmp_path / "knowledge") as store:
        doc = """---
title: "Doc"
namespace: "common"
---
## Summary
Summary text.
## Detailed Rules
Rules text.
"""
        await store.save_node("common/test.md", doc)
        assert store.vector_store.mutation_count >= 1

        # 1. Under threshold: post_task_maintenance does not rebuild
        res1 = await store.post_task_maintenance()
        assert res1["rebuilt"] is False

        # 2. Simulate exceeding mutation threshold
        store.vector_store.mutation_count = 250
        assert store.vector_store.should_rebuild(threshold=200) is True

        # 3. Post-task maintenance rebuilds and resets mutation count
        res2 = await store.post_task_maintenance()
        assert res2["rebuilt"] is True
        assert res2["mutation_count"] == 0
        assert store.vector_store.mutation_count == 0


