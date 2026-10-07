"""Unified KnowledgeStore facade coordinating filesystem, SQLite catalog, and ChromaDB vector store."""

from __future__ import annotations

import fnmatch
import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

from libhippo.models.knowledge import (
    KnowledgeCandidate,
    reconcile_candidate_frontmatter,
    update_markdown_version,
)
from libhippo.storage.catalog import KnowledgeCatalog
from libhippo.storage.mount import (
    MountConfig,
    MountManager,
    create_default_mounts,
)
from libhippo.storage.vector import VectorKnowledgeStore

SectionType = Literal["summary", "rules", "full"]
KnowledgeAction = Literal["create", "update", "split", "merge", "purge", "revalidate"]


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
    content: str
    confidence: float
    importance: float
    force_keep: bool = False
    status: str = "active"


class KnowledgeStore:
    """Unified coordinator for LibHippo dynamic namespace mounts, search, and indexing."""

    def __init__(
        self,
        root_dir: Path | str | None = None,
        mounts: list[MountConfig] | MountManager | None = None,
        catalog: KnowledgeCatalog | None = None,
        vector_store: VectorKnowledgeStore | None = None,
        cache_dir: Path | str | None = None,
    ) -> None:
        if isinstance(mounts, MountManager):
            self.mount_manager = mounts
        elif mounts:
            self.mount_manager = MountManager(mounts)
        elif root_dir is not None:
            r = Path(root_dir)
            self.mount_manager = MountManager(
                mounts=[
                    MountConfig(namespace_prefix="project", physical_path=r / "project", read_only=False),
                    MountConfig(namespace_prefix="common", physical_path=r / "common", read_only=False),
                    MountConfig(namespace_prefix="user", physical_path=r / "user", read_only=False),
                    MountConfig(namespace_prefix="plugins", physical_path=r / "plugins", read_only=False),
                ],
                fallback_root=r,
            )
        else:
            self.mount_manager = create_default_mounts()

        if cache_dir:
            self.cache_dir = Path(cache_dir)
        elif root_dir:
            self.cache_dir = Path(root_dir)
        else:
            proj_mount = self.mount_manager.get_mount("project")
            self.cache_dir = (
                (proj_mount.physical_path / ".cache")
                if proj_mount
                else (Path.cwd() / ".libhippo" / "cache")
            )

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.root_dir = Path(root_dir) if root_dir else self.cache_dir

        self.catalog = catalog or KnowledgeCatalog(self.cache_dir / "knowledge_catalog.db")
        self.vector_store = vector_store or VectorKnowledgeStore(
            persist_dir=self.cache_dir / ".chromadb"
        )
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize storage directories, SQLite catalog, and perform incremental sync."""
        if self._initialized:
            return
        for mount in self.mount_manager.get_all_mounts():
            try:
                mount.physical_path.mkdir(parents=True, exist_ok=True)
            except (OSError, PermissionError):
                pass

        await self.catalog.initialize()
        await self.sync_incremental()
        self._initialized = True

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

    def resolve_fs_path(self, virtual_path: str) -> tuple[Path, MountConfig]:
        """Resolve a logical virtual path to an absolute physical filesystem Path and MountConfig."""
        return self.mount_manager.resolve_virtual_path(virtual_path)

    def _resolve_fs_path(self, rel_path: str) -> Path:
        """Resolve a logical knowledge path to physical filesystem Path (backward compatibility)."""
        path, _ = self.mount_manager.resolve_virtual_path(rel_path)
        return path

    async def sync_incremental(self) -> dict[str, Any]:
        """Synchronize catalog and vector store incrementally with markdown files across mounts.

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

        seen_physical_paths: set[Path] = set()

        for mount in self.mount_manager.get_all_mounts():
            if not mount.physical_path.exists():
                continue
            for md_file in mount.physical_path.rglob("*.md"):
                if "deprecated" in md_file.as_posix() or md_file.name.startswith("."):
                    continue
                resolved_file = md_file.resolve()
                if resolved_file in seen_physical_paths:
                    continue
                seen_physical_paths.add(resolved_file)

                rel_path = self.mount_manager.resolve_physical_path(md_file)
                if not rel_path:
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
                        manifest[rel_path] = {"mtime": st.st_mtime, "sha256": sha}
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
                    manifest[rel_path] = {"mtime": st.st_mtime, "sha256": sha}
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
        """Asynchronous post-task maintenance hook to defragment vector store and check freshness."""
        rebuilt = await self.maybe_rebuild_index()
        from libhippo.storage.freshness import FreshnessChecker

        checker = getattr(self, "freshness_checker", None) or FreshnessChecker()
        batch_res = await checker.check_batch_freshness(self, limit=3)
        return {
            "rebuilt": rebuilt,
            "mutation_count": self.vector_store.mutation_count,
            "freshness_checked": len(batch_res),
            "freshness_results": [r.path for r in batch_res],
        }

    compact_if_needed = post_task_maintenance

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

        seen_physical_paths: set[Path] = set()

        for mount in self.mount_manager.get_all_mounts():
            if not mount.physical_path.exists():
                continue
            for md_file in mount.physical_path.rglob("*.md"):
                if "deprecated" in md_file.as_posix() or md_file.name.startswith("."):
                    continue
                resolved_file = md_file.resolve()
                if resolved_file in seen_physical_paths:
                    continue
                seen_physical_paths.add(resolved_file)

                rel_path = self.mount_manager.resolve_physical_path(md_file)
                if not rel_path:
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
        try:
            fs_path, _ = self.mount_manager.resolve_virtual_path(path)
        except KeyError:
            return None

        if not fs_path.exists():
            return None
        content = fs_path.read_text(encoding="utf-8")
        virtual_path = self.mount_manager.resolve_physical_path(fs_path) or path
        return KnowledgeCandidate.from_markdown(virtual_path, content)

    async def read_knowledge(
        self,
        path: str,
    ) -> str | None:
        """Read knowledge document."""
        candidate = await self.get_node(path)
        if not candidate:
            return None

        await self.catalog.record_access(candidate.path)

        meta_banner = ""
        if candidate.frontmatter:
            fm = candidate.frontmatter
            entry = await self.catalog.get(candidate.path) or {}
            is_web_fetched = (
                fm.namespace == "common"
                or bool(fm.source)
                or bool(fm.version_check)
                or bool(entry.get("source"))
            )
            if is_web_fetched:
                last_updated = (entry.get("last_updated") or fm.last_updated or "unknown")[:10]
                last_checked = (entry.get("last_checked_at") or "never")[:10]
                version = entry.get("version") or fm.version or "1.0.0"
                freshness = entry.get("freshness_status") or "fresh"
                sources = entry.get("source") or fm.source or []
                sources_str = ", ".join(sources) if sources else "none"
                meta_banner = (
                    f"[Knowledge Metadata | version: {version} | last_updated: {last_updated} | "
                    f"last_checked: {last_checked} | status: {freshness} | source: {sources_str}]\n\n"
                )

        return f"{meta_banner}{candidate.markdown}"

    async def save_node(
        self,
        path: str,
        content: str,
        sync_index: bool = True,
        resolve_version: bool = True,
    ) -> KnowledgeCandidate:
        """Save a knowledge document to disk and synchronize catalog and vector index.

        If version_check exists in frontmatter and resolve_version is True,
        fetches the upstream version from that specification/URL and overwrites
        the frontmatter version before saving to disk.
        """
        self.mount_manager.check_writable(path)
        fs_path, _ = self.mount_manager.resolve_virtual_path(path)
        fs_path.parent.mkdir(parents=True, exist_ok=True)

        virtual_path = self.mount_manager.resolve_physical_path(fs_path) or path
        candidate = KnowledgeCandidate.from_markdown(virtual_path, content)

        # Silently drop agent-provided 2~4 properties and reconcile with previous disk state
        existing_candidate = None
        if fs_path.exists():
            try:
                prev_text = fs_path.read_text(encoding="utf-8")
                existing_candidate = KnowledgeCandidate.from_markdown(virtual_path, prev_text)
            except Exception:
                pass

        if candidate.frontmatter:
            reconcile_candidate_frontmatter(candidate, previous_candidate=existing_candidate)
            content = candidate.to_markdown()

        fetched_upstream: str | None = None
        if resolve_version and candidate.frontmatter and candidate.frontmatter.version_check:
            from libhippo.storage.freshness import FreshnessChecker

            checker = getattr(self, "freshness_checker", None) or FreshnessChecker()
            upstream_version, _ = await checker.fetch_upstream_version(
                candidate.frontmatter.version_check,
                candidate.frontmatter.source,
            )
            if upstream_version:
                content = update_markdown_version(content, upstream_version)
                candidate = KnowledgeCandidate.from_markdown(virtual_path, content)
                fetched_upstream = upstream_version

        fs_path.write_text(content, encoding="utf-8")

        st = fs_path.stat()
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

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

            if fetched_upstream:
                from datetime import UTC, datetime

                now_iso = datetime.now(UTC).isoformat()
                await self.catalog.update_freshness(
                    path=virtual_path,
                    freshness_status="fresh",
                    upstream_version=fetched_upstream,
                    last_checked_at=now_iso,
                    stale_reason=None,
                    cascade_children=True,
                )

        return candidate

    async def delete_node(self, path: str, force: bool = False) -> bool:
        """Delete a node from disk, catalog, and vector index."""
        self.mount_manager.check_writable(path)
        candidate = await self.get_node(path)
        if candidate and candidate.frontmatter and candidate.frontmatter.force_keep and not force:
            raise ValueError(f"Cannot delete node '{path}' with force_keep=True unless force=True")

        fs_path, _ = self.mount_manager.resolve_virtual_path(path)
        virtual_path = self.mount_manager.resolve_physical_path(fs_path) or path

        deleted = False
        if fs_path.exists():
            fs_path.unlink()
            deleted = True

        await self.catalog.delete(virtual_path)
        self.vector_store.delete(virtual_path)
        return deleted

    async def deprecate_node(self, path: str, reason: str = "", force: bool = False) -> str:
        """Quarantine a knowledge file by moving it into the deprecated/ scope."""
        self.mount_manager.check_writable(path)
        candidate = await self.get_node(path)
        if candidate and candidate.frontmatter and candidate.frontmatter.force_keep and not force:
            raise ValueError(f"Cannot deprecate node '{path}' with force_keep=True unless force=True")

        fs_path, mount = self.mount_manager.resolve_virtual_path(path)
        virtual_path = self.mount_manager.resolve_physical_path(fs_path) or path

        if not fs_path.exists():
            raise FileNotFoundError(f"Knowledge node not found: {path}")

        deprecated_fs_path = mount.physical_path / "deprecated" / fs_path.name
        deprecated_fs_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(fs_path), str(deprecated_fs_path))

        await self.catalog.delete(virtual_path)
        self.vector_store.delete(virtual_path)

        return deprecated_fs_path.as_posix()


    async def search(
        self,
        query: str,
        namespace: str | None = None,
        top_k: int = 5,
        alpha: float = 0.08,
    ) -> list[KnowledgeQueryResult]:
        """Search knowledge using vector similarity boosted by importance confidence."""
        if not self._initialized:
            await self.initialize()

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
                    content=vr.content,
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
                        content=fts.get("summary") or fts["title"],
                        confidence=round(0.50 + 0.10 * float(fts["importance"]), 4),
                        importance=float(fts["importance"]),
                        force_keep=bool(fts.get("force_keep", False)),
                    )
                )

        return results

    async def improve_search_confidence(
        self,
        path: str,
        keywords: list[str],
        remove_tags: list[str] | None = None,
    ) -> bool:
        """Persist newly identified search keywords into document frontmatter tags and update vector index."""
        candidate = await self.get_node(path)
        if not candidate or not candidate.frontmatter:
            return False

        from libhippo.models.knowledge import MAX_TAGS, normalize_tags

        changed = False
        current_tags = list(candidate.frontmatter.tags)

        # 1. Strictly restricted removal of previous tags
        if remove_tags:
            remove_targets = [r.strip().lower() for r in remove_tags if r.strip()][:2]
            for target in remove_targets:
                if len(current_tags) <= 1:
                    break
                for idx, t in enumerate(current_tags):
                    if t.strip().lower() == target:
                        current_tags.pop(idx)
                        changed = True
                        break

        # 2. Addition of new keywords up to MAX_TAGS
        if keywords:
            existing_set = {t.strip().lower() for t in current_tags}
            for k in keywords:
                clean_k = k.strip().lower()
                if clean_k and clean_k not in existing_set:
                    if len(current_tags) >= MAX_TAGS:
                        break
                    current_tags.append(clean_k)
                    existing_set.add(clean_k)
                    changed = True

        if not changed:
            return False

        candidate.frontmatter.tags = normalize_tags(current_tags)
        await self.save_node(path, candidate.to_markdown(), sync_index=True)
        return True

    async def record_search_alias(self, path: str, query: str, decay: float = 0.80) -> None:
        """Update the decayed search alias vector centroid without mutating markdown frontmatter."""
        if not self._initialized:
            await self.initialize()
        self.vector_store.update_alias_centroid(path=path, query=query, decay=decay)

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
        if not self._initialized:
            await self.initialize()

        metadata = metadata or {}
        extra_paths = extra_paths or []

        self.mount_manager.check_writable(path)
        for ep in extra_paths:
            self.mount_manager.check_writable(ep)

        if action in ("create", "update"):
            if not content and action == "update":
                existing_node = await self.get_node(path)
                if existing_node:
                    content = existing_node.markdown

            candidate = await self.save_node(path, content, sync_index=True)
            return {
                "status": "success",
                "action": action,
                "path": candidate.path,
                "title": candidate.frontmatter.title if candidate.frontmatter else "",
                "version": candidate.frontmatter.version if candidate.frontmatter else "",
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

        elif action == "revalidate":
            from libhippo.storage.freshness import FreshnessChecker

            checker = getattr(self, "freshness_checker", None) or FreshnessChecker()
            freshness_res = await checker.check_node_freshness(path=path, store=self, force=True)

            curator = getattr(self, "curator", None)
            if freshness_res.status == "stale" and curator:
                try:
                    curation = await curator.curate(
                        topic_or_query=f"{path} version {freshness_res.upstream_version}",
                        target_path=path,
                    )
                    draft = curation.get("draft") if isinstance(curation, dict) else str(curation)
                    if draft:
                        await self.save_node(path, draft, sync_index=True)
                        from datetime import UTC, datetime

                        now_str = datetime.now(UTC).isoformat()
                        await self.catalog.update_freshness(
                            path=path,
                            freshness_status="fresh",
                            last_checked_at=now_str,
                            upstream_version=freshness_res.upstream_version,
                            stale_reason=None,
                            cascade_children=True,
                        )
                        return {
                            "status": "success",
                            "action": "revalidate",
                            "path": path,
                            "revalidation": "updated",
                            "old_version": freshness_res.current_version,
                            "new_version": freshness_res.upstream_version,
                        }
                except Exception as e:  # noqa: BLE001
                    pass

            return {
                "status": "success",
                "action": "revalidate",
                "path": path,
                "revalidation": freshness_res.status,
                "current_version": freshness_res.current_version,
                "upstream_version": freshness_res.upstream_version,
                "message": freshness_res.message,
            }

        raise ValueError(f"Unsupported modify_knowledge action: {action}")

    async def list_knowledge(self, path: str = ".", max_depth: int = 2) -> str:
        """Structural listing of knowledge namespaces and documents behaving like list_dir.

        Zero curation, zero version checking, zero LLM calls.
        """
        lines: list[str] = []
        if path in (".", "", "/"):
            lines.append("[Knowledge Mounts]")
            mounts = self.mount_manager.get_all_mounts()
            for idx, m in enumerate(mounts):
                is_last_m = idx == len(mounts) - 1
                connector = "└── " if is_last_m else "├── "
                child_prefix = "    " if is_last_m else "│   "
                lines.append(f"{connector}{m.namespace_prefix}/")
                if m.physical_path.exists() and m.physical_path.is_dir():
                    self._build_knowledge_tree(
                        m.physical_path,
                        child_prefix,
                        current_depth=1,
                        max_depth=max_depth,
                        lines=lines,
                    )
        else:
            norm = path.strip("/.")
            try:
                target_phys = self.mount_manager.resolve_virtual_path(norm)[0]
            except Exception:
                target_phys = self.root_dir / norm

            if not target_phys.exists():
                return f"[Knowledge directory or node not found: '{path}']"
            if target_phys.is_file():
                return f"{norm} (file)"
            lines.append(f"{norm}/")
            self._build_knowledge_tree(
                target_phys,
                "",
                current_depth=1,
                max_depth=max_depth,
                lines=lines,
            )
        return "\n".join(lines)

    def _build_knowledge_tree(
        self,
        current: Path,
        prefix: str,
        current_depth: int,
        max_depth: int,
        lines: list[str],
    ) -> None:
        if current_depth > max_depth:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return

        visible = [
            e
            for e in entries
            if not e.name.startswith(".") and (e.is_dir() or e.name.endswith(".md"))
        ]
        count = len(visible)
        for idx, entry in enumerate(visible):
            is_last = idx == count - 1
            connector = "└── " if is_last else "├── "
            child_prefix = "    " if is_last else "│   "
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                self._build_knowledge_tree(
                    entry, prefix + child_prefix, current_depth + 1, max_depth, lines
                )
            else:
                lines.append(f"{prefix}{connector}{entry.name}")

    async def search_knowledge(
        self,
        path: str = ".",
        pattern: str = "*",
        content_pattern: str | None = None,
    ) -> str:
        """Fast lexical search matching path globs and/or content regex across knowledge documents.

        Zero curation, zero version checking, zero LLM calls.
        """
        matches: list[str] = []
        c_regex = re.compile(content_pattern, re.IGNORECASE) if content_pattern else None

        search_dirs: list[tuple[str, Path]] = []
        if path in (".", "", "/"):
            for m in self.mount_manager.get_all_mounts():
                if m.physical_path.exists():
                    search_dirs.append((m.namespace_prefix, m.physical_path))
        else:
            norm = path.strip("/.")
            try:
                phys = self.mount_manager.resolve_virtual_path(norm)[0]
            except Exception:
                phys = self.root_dir / norm
            if phys.exists():
                search_dirs.append((norm, phys))

        for base_ns, root in search_dirs:
            if root.is_file():
                files = [root]
            else:
                files = [
                    p
                    for p in root.rglob("*.md")
                    if not any(part.startswith(".") for part in p.parts)
                ]

            for f in sorted(files):
                rel_parts = f.relative_to(root).as_posix() if root.is_dir() else f.name
                virt_path = f"{base_ns}/{rel_parts}" if root.is_dir() and rel_parts != "." else base_ns
                virt_path = virt_path.replace("//", "/")

                if pattern and pattern != "*":
                    if not fnmatch.fnmatch(virt_path, pattern) and not fnmatch.fnmatch(f.name, pattern):
                        continue

                if c_regex:
                    try:
                        text = f.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    file_matches = []
                    for line_no, line in enumerate(text.splitlines(), start=1):
                        if c_regex.search(line):
                            file_matches.append(f"  {line_no}: {line.strip()[:150]}")
                    if file_matches:
                        matches.append(f"{virt_path}:\n" + "\n".join(file_matches[:10]))
                else:
                    matches.append(virt_path)

        if not matches:
            return "No matching knowledge documents found."
        return "\n".join(matches)

