"""Unified KnowledgeStore facade coordinating filesystem, SQLite catalog, and ChromaDB vector store."""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

from libhippo.models.knowledge import KnowledgeCandidate
from libhippo.storage.catalog import KnowledgeCatalog
from libhippo.storage.vector import VectorKnowledgeStore

SectionType = Literal["summary", "rules", "full"]
KnowledgeAction = Literal["create", "update", "split", "merge", "purge"]


def extract_sections(markdown: str) -> tuple[str, str]:
    """Extract coarse summary and fine detailed rules sections from markdown body."""
    summary_match = re.search(
        r"##\s+Summary(?:\s*\([^)]*\))?\s*\n(.*?)(?=\n##|\Z)",
        markdown,
        re.DOTALL | re.IGNORECASE,
    )
    rules_match = re.search(
        r"##\s+Detailed Rules(?:\s*(?:&|and)\s*Edge Cases)?(?:\s*\([^)]*\))?\s*\n(.*?)(?=\n##|\Z)",
        markdown,
        re.DOTALL | re.IGNORECASE,
    )

    summary = summary_match.group(1).strip() if summary_match else ""
    rules = rules_match.group(1).strip() if rules_match else ""

    # Fallback if specific section headers are absent
    if not summary and not rules:
        body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", markdown, flags=re.DOTALL).strip()
        parts = body.split("\n\n", 1)
        summary = parts[0].strip() if parts else ""
        rules = parts[1].strip() if len(parts) > 1 else ""

    return summary, rules


@dataclass
class KnowledgeQueryResult:
    """Consolidated search result combining vector scoring and catalog metadata."""

    path: str
    title: str
    namespace: str
    section: str
    snippet: str
    confidence: float
    importance: float
    force_keep: bool = False
    status: str = "active"


class KnowledgeStore:
    """Unified coordinator for LibHippo knowledge persistence, search, and indexing."""

    def __init__(
        self,
        root_dir: Path | str,
        catalog: KnowledgeCatalog | None = None,
        vector_store: VectorKnowledgeStore | None = None,
    ) -> None:
        self.root_dir = Path(root_dir)
        self.catalog = catalog or KnowledgeCatalog(self.root_dir / "knowledge_catalog.db")
        self.vector_store = vector_store or VectorKnowledgeStore(
            persist_dir=self.root_dir / ".chromadb"
        )

    async def initialize(self) -> None:
        """Initialize storage directories, SQLite catalog, and perform incremental sync."""
        self.root_dir.mkdir(parents=True, exist_ok=True)
        for ns in ["common", "user", "project", "plugins", "deprecated"]:
            (self.root_dir / ns).mkdir(parents=True, exist_ok=True)

        await self.catalog.initialize()
        await self.sync_incremental()

    async def close(self) -> None:
        """Close storage catalog and release database connections."""
        await self.catalog.close()

    async def __aenter__(self) -> Self:
        await self.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await self.close()

    def _resolve_fs_path(self, rel_path: str) -> Path:
        """Resolve a logical knowledge path to an absolute filesystem Path."""
        clean_path = rel_path.lstrip("/").replace("\\", "/")
        if not clean_path.endswith(".md"):
            clean_path = f"{clean_path}.md"
        return self.root_dir / clean_path

    async def sync_incremental(self) -> dict[str, int]:
        """Synchronize catalog and vector store incrementally with markdown files on disk.

        Applies 3-tier check:
        1. Filesystem mtime (skips unchanged files)
        2. Content SHA-256 (handles touch/git checkout without re-embedding)
        3. Selective update & orphan pruning
        """
        manifest = await self.catalog.get_sync_manifest()
        disk_paths: set[str] = set()
        stats: dict[str, Any] = {
            "scanned": 0,
            "skipped": 0,
            "updated": 0,
            "pruned": 0,
            "upserted": 0,
            "deleted": 0,
            "mtime_only_updated": 0,
            "details": {"upserted": [], "deleted": []},
        }

        for md_file in self.root_dir.rglob("*.md"):
            rel_path = md_file.relative_to(self.root_dir).as_posix()
            if "deprecated" in rel_path or md_file.name.startswith("."):
                continue
            disk_paths.add(rel_path)
            stats["scanned"] += 1

            try:
                st = md_file.stat()
                cached = manifest.get(rel_path)
                if cached and cached["mtime"] == st.st_mtime:
                    stats["skipped"] += 1
                    continue

                content = md_file.read_text(encoding="utf-8")
                sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

                if cached and cached["sha256"] == sha:
                    # Content identical, refresh mtime in DB
                    await self.catalog.update_mtime(rel_path, st.st_mtime)
                    stats["skipped"] += 1
                    stats["mtime_only_updated"] += 1
                    continue

                candidate = KnowledgeCandidate.from_markdown(rel_path, content)
                summary, rules = extract_sections(candidate.body)
                await self.catalog.upsert(
                    candidate,
                    summary=summary,
                    rules=rules,
                    file_mtime=st.st_mtime,
                    content_sha256=sha,
                )
                self.vector_store.upsert(candidate, summary=summary, rules=rules)
                stats["updated"] += 1
                stats["upserted"] += 1
                stats["details"]["upserted"].append(rel_path)
            except Exception:  # noqa: BLE001, S112
                continue

        # Orphan pruning: remove paths in catalog that no longer exist on disk
        for cat_path in manifest:
            if cat_path not in disk_paths:
                await self.catalog.delete(cat_path)
                self.vector_store.delete(cat_path)
                stats["pruned"] += 1
                stats["deleted"] += 1
                stats["details"]["deleted"].append(cat_path)

        return stats

    async def maybe_rebuild_index(self) -> bool:
        """Check if mutation churn threshold is met and rebuild index if needed."""
        if self.vector_store.should_rebuild():
            await self.rebuild_index()
            return True
        return False

    async def post_task_maintenance(self) -> dict[str, Any]:
        """Asynchronous post-task maintenance hook to defragment vector store in background."""
        rebuilt = await self.maybe_rebuild_index()
        return {"rebuilt": rebuilt, "mutation_count": self.vector_store.mutation_count}

    async def sync_all(self) -> dict[str, Any]:
        """Alias for sync_incremental."""

        return await self.sync_incremental()

    async def sync_from_filesystem(self) -> dict[str, Any]:
        """Alias for sync_incremental."""
        return await self.sync_incremental()

    async def rebuild_index(self) -> None:


        """Full clean rebuild of derived SQLite catalog and ChromaDB vector store."""
        self.vector_store.reset()
        await self.catalog.clear()

        for md_file in self.root_dir.rglob("*.md"):
            rel_path = md_file.relative_to(self.root_dir).as_posix()
            if "deprecated" in rel_path or md_file.name.startswith("."):
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
                st = md_file.stat()
                sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
                candidate = KnowledgeCandidate.from_markdown(rel_path, content)
                summary, rules = extract_sections(candidate.body)
                await self.catalog.upsert(
                    candidate,
                    summary=summary,
                    rules=rules,
                    file_mtime=st.st_mtime,
                    content_sha256=sha,
                )
                self.vector_store.upsert(candidate, summary=summary, rules=rules)
            except Exception:  # noqa: BLE001, S112
                continue

        self.vector_store.mutation_count = 0

    async def get_node(self, path: str) -> KnowledgeCandidate | None:

        """Retrieve a knowledge document from disk as a KnowledgeCandidate."""
        fs_path = self._resolve_fs_path(path)
        if not fs_path.exists():
            return None
        content = fs_path.read_text(encoding="utf-8")
        clean_path = fs_path.relative_to(self.root_dir).as_posix()
        return KnowledgeCandidate.from_markdown(clean_path, content)

    async def read_section(
        self,
        path: str,
        section: SectionType = "full",
    ) -> str | None:
        """Read a specified section ('summary', 'rules', 'full') from knowledge file."""
        candidate = await self.get_node(path)
        if not candidate:
            return None

        await self.catalog.record_access(candidate.path)

        if section == "full":
            return candidate.markdown

        summary, rules = extract_sections(candidate.body)
        if section == "summary":
            return summary or candidate.body
        elif section == "rules":
            return rules or candidate.body
        return candidate.markdown

    async def save_node(
        self,
        path: str,
        content: str,
        sync_index: bool = True,
    ) -> KnowledgeCandidate:
        """Save a knowledge document to disk and synchronize catalog and vector index."""
        fs_path = self._resolve_fs_path(path)
        fs_path.parent.mkdir(parents=True, exist_ok=True)
        fs_path.write_text(content, encoding="utf-8")

        st = fs_path.stat()
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

        clean_path = fs_path.relative_to(self.root_dir).as_posix()
        candidate = KnowledgeCandidate.from_markdown(clean_path, content)

        if sync_index and candidate.frontmatter:
            summary, rules = extract_sections(candidate.body)
            await self.catalog.upsert(
                candidate,
                summary=summary,
                rules=rules,
                file_mtime=st.st_mtime,
                content_sha256=sha,
            )
            self.vector_store.upsert(candidate, summary=summary, rules=rules)

        return candidate

    async def delete_node(self, path: str, force: bool = False) -> bool:
        """Delete a node from disk, catalog, and vector index."""
        candidate = await self.get_node(path)
        if candidate and candidate.frontmatter and candidate.frontmatter.force_keep and not force:
            raise ValueError(f"Cannot delete node '{path}' with force_keep=True unless force=True")

        fs_path = self._resolve_fs_path(path)
        clean_path = fs_path.relative_to(self.root_dir).as_posix()

        deleted = False
        if fs_path.exists():
            fs_path.unlink()
            deleted = True

        await self.catalog.delete(clean_path)
        self.vector_store.delete(clean_path)
        return deleted

    async def deprecate_node(self, path: str, reason: str = "", force: bool = False) -> str:
        """Quarantine a knowledge file by moving it into the deprecated/ scope."""
        candidate = await self.get_node(path)
        if candidate and candidate.frontmatter and candidate.frontmatter.force_keep and not force:
            raise ValueError(f"Cannot deprecate node '{path}' with force_keep=True unless force=True")

        fs_path = self._resolve_fs_path(path)
        clean_path = fs_path.relative_to(self.root_dir).as_posix()

        if not fs_path.exists():
            raise FileNotFoundError(f"Knowledge node not found: {path}")

        deprecated_fs_path = self.root_dir / "deprecated" / clean_path
        deprecated_fs_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(fs_path), str(deprecated_fs_path))

        await self.catalog.delete(clean_path)
        self.vector_store.delete(clean_path)

        return deprecated_fs_path.relative_to(self.root_dir).as_posix()

    async def search(
        self,
        query: str,
        namespace: str | None = None,
        top_k: int = 5,
        alpha: float = 0.08,
    ) -> list[KnowledgeQueryResult]:
        """Search knowledge using vector similarity boosted by importance confidence."""
        vector_results = self.vector_store.search(
            query=query,
            namespace=namespace,
            top_k=top_k,
            alpha=alpha,
        )

        results: list[KnowledgeQueryResult] = []
        for vr in vector_results:
            results.append(
                KnowledgeQueryResult(
                    path=vr.path,
                    title=vr.title,
                    namespace=vr.namespace,
                    section=vr.section,
                    snippet=vr.snippet,
                    confidence=vr.confidence,
                    importance=vr.importance,
                )
            )

        # Fallback to FTS if vector index is empty or yielded no results
        if not results:
            fts_matches = await self.catalog.search_fts(query=query, namespace=namespace, limit=top_k)
            for fts in fts_matches:
                results.append(
                    KnowledgeQueryResult(
                        path=fts["path"],
                        title=fts["title"],
                        namespace=fts["namespace"],
                        section="full",
                        snippet=fts.get("summary") or fts["title"],
                        confidence=round(0.50 + 0.10 * float(fts["importance"]), 4),
                        importance=float(fts["importance"]),
                        force_keep=bool(fts.get("force_keep", False)),
                    )
                )

        return results

    async def modify_knowledge(
        self,
        action: KnowledgeAction,
        path: str,
        content: str = "",
        metadata: dict[str, Any] | None = None,
        extra_paths: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Atomic disk mutation tool with automatic index re-synchronization."""
        metadata = metadata or {}
        extra_paths = extra_paths or []

        if action in ("create", "update"):
            candidate = await self.save_node(path, content, sync_index=True)
            return {
                "status": "success",
                "action": action,
                "path": candidate.path,
                "title": candidate.frontmatter.title if candidate.frontmatter else "",
            }

        elif action == "split":
            # If main path has force_keep, ensure we preserve it
            if content:
                await self.save_node(path, content, sync_index=True)

            split_paths = [path, *extra_paths]
            return {
                "status": "success",
                "action": "split",
                "paths": split_paths,
            }

        elif action == "merge":
            # Check force_keep on coalesced paths
            for sibling_path in extra_paths:
                sibling_candidate = await self.get_node(sibling_path)
                if (
                    sibling_candidate
                    and sibling_candidate.frontmatter
                    and sibling_candidate.frontmatter.force_keep
                    and not force
                ):
                    raise ValueError(
                        f"Cannot coalesce node '{sibling_path}' with force_keep=True into '{path}' unless force=True"
                    )

            candidate = await self.save_node(path, content, sync_index=True)
            purged = []
            for sibling_path in extra_paths:
                if sibling_path != path:
                    await self.delete_node(sibling_path, force=force)
                    purged.append(sibling_path)

            return {
                "status": "success",
                "action": "merge",
                "target_path": candidate.path,
                "coalesced_paths": purged,
            }

        elif action == "purge":
            deprecated_path = await self.deprecate_node(
                path,
                reason=metadata.get("reason", ""),
                force=force,
            )
            return {
                "status": "success",
                "action": "purge",
                "path": path,
                "quarantined_to": deprecated_path,
            }

        raise ValueError(f"Unsupported modify_knowledge action: {action}")
