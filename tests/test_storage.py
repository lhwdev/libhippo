"""Unit tests for KnowledgeStorage, KnowledgeCatalog, and VectorKnowledgeStore."""

import pytest

from libhippo.models.knowledge import KnowledgeCandidate
from libhippo.storage.catalog import KnowledgeCatalog
from libhippo.storage.mount import MountConfig, MountManager, ReadOnlyMountError
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
    # Verify importance confidence formula: (1 - alpha) * cosine_sim + alpha * importance
    expected_conf = min(1.0, round(0.92 * top.cosine_sim + 0.08 * 0.85, 4))
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

        # 2. Read knowledge
        content = await store.read_knowledge("plugins/react19/actions.md")
        assert "useActionState replaces useFormState" in content
        assert "useActionState(fn, initialState)" in content

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
        content = await store.read_knowledge("common/file1.md")
        assert "Updated summary." in content


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


def test_mount_manager_resolution(tmp_path):
    """Verify virtual path resolution, physical reverse resolution, and writable checks."""
    proj_dir = tmp_path / "project_root"
    comm_dir = tmp_path / "common_root"

    manager = MountManager([
        MountConfig(namespace_prefix="project", physical_path=proj_dir, read_only=False),
        MountConfig(namespace_prefix="common", physical_path=comm_dir, read_only=True),
    ])

    # Virtual to physical
    phys, mount = manager.resolve_virtual_path("project/web/api.md")
    assert phys == proj_dir / "web" / "api.md"
    assert mount.read_only is False

    phys_ro, mount_ro = manager.resolve_virtual_path("common/python/guidelines.md")
    assert phys_ro == comm_dir / "python" / "guidelines.md"
    assert mount_ro.read_only is True

    # Writable check
    assert manager.check_writable("project/web/api.md") is mount
    with pytest.raises(ReadOnlyMountError):
        manager.check_writable("common/python/guidelines.md")

    # Physical to virtual
    assert manager.resolve_physical_path(comm_dir / "python" / "guidelines.md") == "common/python/guidelines.md"


@pytest.mark.asyncio
async def test_knowledge_store_read_only_mount(tmp_path):
    """Verify that read-only mounts reject mutations while writable mounts accept them."""
    proj_dir = tmp_path / "project"
    comm_dir = tmp_path / "common"
    comm_dir.mkdir(parents=True, exist_ok=True)

    # Pre-populate a common rule on disk
    ro_file = comm_dir / "base_rules.md"
    ro_file.write_text(
        """---
title: "Base Rules"
namespace: "common"
---
## Summary
Common base rules.
## Detailed Rules
Immutable shared rules.
""",
        encoding="utf-8",
    )

    mounts = [
        MountConfig(namespace_prefix="project", physical_path=proj_dir, read_only=False),
        MountConfig(namespace_prefix="common", physical_path=comm_dir, read_only=True),
    ]

    async with KnowledgeStore(mounts=mounts, cache_dir=tmp_path / "cache") as store:
        # Writable mount should succeed
        node = await store.save_node(
            "project/app.md",
            """---
title: "App Rules"
namespace: "project"
---
## Summary
App summary.
## Detailed Rules
App rules.
""",
        )
        assert node.path == "project/app.md"

        # Direct mutation on read-only mount should raise ReadOnlyMountError
        with pytest.raises(ReadOnlyMountError):
            await store.save_node("common/new_rule.md", "content")

        with pytest.raises(ReadOnlyMountError):
            await store.delete_node("common/base_rules.md", force=True)

        with pytest.raises(ReadOnlyMountError):
            await store.deprecate_node("common/base_rules.md", force=True)

        # modify_knowledge tool actions on read-only mount should fail
        with pytest.raises(ReadOnlyMountError):
            await store.modify_knowledge(action="create", path="common/new.md", content="new")

        with pytest.raises(ReadOnlyMountError):
            await store.modify_knowledge(action="update", path="common/base_rules.md", content="updated")

        with pytest.raises(ReadOnlyMountError):
            await store.modify_knowledge(
                action="merge",
                path="project/app.md",
                extra_paths=["common/base_rules.md"],
            )

        with pytest.raises(ReadOnlyMountError):
            await store.modify_knowledge(action="purge", path="common/base_rules.md", force=True)


@pytest.mark.asyncio
async def test_knowledge_store_multi_mount_sync_and_search(tmp_path):
    """Verify that multi-mount stores index and search documents across all mounts."""
    proj_dir = tmp_path / "proj"
    comm_dir = tmp_path / "comm"
    cache_dir = tmp_path / "cache"

    comm_dir.mkdir(parents=True, exist_ok=True)
    (comm_dir / "react_base.md").write_text(
        """---
title: "React Common Rules"
namespace: "common"
importance: 0.8
---
## Summary
Always use functional components in React.
## Detailed Rules
Do not use class components.
""",
        encoding="utf-8",
    )

    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "react_override.md").write_text(
        """---
title: "React Project Override"
namespace: "project"
importance: 0.95
---
## Summary
Use custom hook useData for React fetching in this project.
## Detailed Rules
Wrap data fetches in useData.
""",
        encoding="utf-8",
    )

    mounts = [
        MountConfig(namespace_prefix="project", physical_path=proj_dir, read_only=False),
        MountConfig(namespace_prefix="common", physical_path=comm_dir, read_only=True),
    ]

    async with KnowledgeStore(mounts=mounts, cache_dir=cache_dir) as store:
        results = await store.search("React components", top_k=5)
        paths = [r.path for r in results]
        namespaces = {r.namespace for r in results}

        assert "common/react_base.md" in paths
        assert "project/react_override.md" in paths
        assert namespaces == {"common", "project"}


