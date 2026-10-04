"""Local SQLite catalog and FTS5 index for LibHippo knowledge nodes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

import aiosqlite
import aiosqlite.core
from threading import Thread

if not getattr(aiosqlite.core, "_libhippo_daemon_patched", False):
    _orig_thread = aiosqlite.core.Thread

    class _DaemonThread(_orig_thread):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["daemon"] = True
            super().__init__(*args, **kwargs)

    aiosqlite.core.Thread = _DaemonThread
    aiosqlite.core._libhippo_daemon_patched = True  # type: ignore[attr-defined]

from libhippo.models.knowledge import KnowledgeCandidate, KnowledgeFrontmatter


class KnowledgeCatalog:
    """Manages metadata persistence and FTS5 full-text indexing in SQLite."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def _get_conn(self) -> aiosqlite.Connection:
        """Get or lazily create a persistent reusable SQLite connection."""
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self.db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA journal_mode = WAL;")
            await self._conn.execute("PRAGMA synchronous = NORMAL;")
        return self._conn

    async def close(self) -> None:
        """Close the active database connection if open."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> Self:
        await self._get_conn()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await self.close()

    async def initialize(self) -> None:
        """Create tables and FTS5 virtual tables if they do not exist."""
        db = await self._get_conn()
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_entries (
                path TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                namespace TEXT NOT NULL,
                version TEXT NOT NULL,
                status TEXT NOT NULL,
                nature TEXT NOT NULL,
                importance REAL NOT NULL,
                force_keep INTEGER NOT NULL DEFAULT 0,
                file_mtime REAL NOT NULL DEFAULT 0.0,
                content_sha256 TEXT NOT NULL DEFAULT '',
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed TEXT,
                last_updated TEXT,
                tags TEXT NOT NULL,
                related TEXT NOT NULL
            )
            """
        )

        # Migration check: Ensure new columns exist if DB was created previously
        async with db.execute("PRAGMA table_info(catalog_entries)") as cursor:
            existing_cols = {row["name"] for row in await cursor.fetchall()}
            if "force_keep" not in existing_cols:
                await db.execute("ALTER TABLE catalog_entries ADD COLUMN force_keep INTEGER NOT NULL DEFAULT 0")
            if "file_mtime" not in existing_cols:
                await db.execute("ALTER TABLE catalog_entries ADD COLUMN file_mtime REAL NOT NULL DEFAULT 0.0")
            if "content_sha256" not in existing_cols:
                await db.execute("ALTER TABLE catalog_entries ADD COLUMN content_sha256 TEXT NOT NULL DEFAULT ''")

        await db.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS catalog_fts USING fts5(
                path UNINDEXED,
                title,
                tags,
                summary,
                rules,
                body,
                tokenize = 'porter unicode61'
            )
            """
        )
        await db.commit()

    async def upsert(
        self,
        candidate: KnowledgeCandidate,
        summary: str = "",
        rules: str = "",
        file_mtime: float = 0.0,
        content_sha256: str = "",
    ) -> None:
        """Insert or update a node's metadata, sync hashes, and FTS content."""
        fm = candidate.frontmatter or KnowledgeFrontmatter(
            title=Path(candidate.path).stem,
            namespace="common",
        )
        now_str = datetime.now(UTC).isoformat()
        last_updated = fm.last_updated or now_str
        db = await self._get_conn()

        await db.execute(
            """
            INSERT INTO catalog_entries (
                path, title, namespace, version, status, nature,
                importance, force_keep, file_mtime, content_sha256,
                access_count, last_accessed, last_updated,
                tags, related
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                title=excluded.title,
                namespace=excluded.namespace,
                version=excluded.version,
                status=excluded.status,
                nature=excluded.nature,
                importance=excluded.importance,
                force_keep=excluded.force_keep,
                file_mtime=excluded.file_mtime,
                content_sha256=excluded.content_sha256,
                last_updated=excluded.last_updated,
                tags=excluded.tags,
                related=excluded.related
            """,
            (
                candidate.path,
                fm.title,
                fm.namespace,
                fm.version,
                fm.status,
                fm.nature,
                fm.importance,
                1 if fm.force_keep else 0,
                file_mtime,
                content_sha256,
                fm.access_count,
                fm.last_accessed,
                last_updated,
                json.dumps(fm.tags),
                json.dumps(fm.related),
            ),
        )

        # Update FTS5 table
        await db.execute("DELETE FROM catalog_fts WHERE path = ?", (candidate.path,))
        await db.execute(
            """
            INSERT INTO catalog_fts (path, title, tags, summary, rules, body)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                candidate.path,
                fm.title,
                " ".join(fm.tags),
                summary,
                rules,
                candidate.body,
            ),
        )
        await db.commit()

    async def update_mtime(self, path: str, mtime: float) -> None:
        """Update only file_mtime without modifying content or re-indexing."""
        db = await self._get_conn()
        await db.execute(
            "UPDATE catalog_entries SET file_mtime = ? WHERE path = ?",
            (mtime, path),
        )
        await db.commit()

    async def get_sync_manifest(self) -> dict[str, dict[str, Any]]:
        """Fetch lightweight sync manifest {path: {'mtime': float, 'sha256': str}}."""
        db = await self._get_conn()
        async with db.execute(
            "SELECT path, file_mtime, content_sha256 FROM catalog_entries",
        ) as cursor:
            rows = await cursor.fetchall()
            return {
                row["path"]: {
                    "mtime": float(row["file_mtime"]),
                    "sha256": str(row["content_sha256"]),
                }
                for row in rows
            }

    async def clear(self) -> None:
        """Clear all entries from catalog and FTS."""
        db = await self._get_conn()
        await db.execute("DELETE FROM catalog_entries")
        await db.execute("DELETE FROM catalog_fts")
        await db.commit()

    async def delete(self, path: str) -> None:
        """Remove a node from catalog and FTS."""
        db = await self._get_conn()
        await db.execute("DELETE FROM catalog_entries WHERE path = ?", (path,))
        await db.execute("DELETE FROM catalog_fts WHERE path = ?", (path,))
        await db.commit()

    async def get(self, path: str) -> dict[str, Any] | None:
        """Fetch node metadata by path."""
        db = await self._get_conn()
        async with db.execute(
            "SELECT * FROM catalog_entries WHERE path = ?",
            (path,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["force_keep"] = bool(data["force_keep"])
            data["tags"] = json.loads(data["tags"])
            data["related"] = json.loads(data["related"])
            return data

    async def record_access(self, path: str) -> None:
        """Increment access count and timestamp."""
        now_str = datetime.now(UTC).isoformat()
        db = await self._get_conn()
        await db.execute(
            """
            UPDATE catalog_entries
            SET access_count = access_count + 1, last_accessed = ?
            WHERE path = ?
            """,
            (now_str, path),
        )
        await db.commit()

    async def search_fts(
        self,
        query: str,
        namespace: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Query FTS5 virtual table for keyword matches."""
        clean_terms = [t for t in query.split() if t.isalnum()]
        if not clean_terms:
            return []
        fts_query = " OR ".join(clean_terms)

        sql = """
            SELECT e.*, bm25(catalog_fts) as rank
            FROM catalog_fts f
            JOIN catalog_entries e ON f.path = e.path
            WHERE catalog_fts MATCH ? AND e.status = 'active'
        """
        params: list[Any] = [fts_query]

        if namespace:
            sql += " AND e.namespace = ?"
            params.append(namespace)

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        db = await self._get_conn()
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                item = dict(row)
                item["force_keep"] = bool(item["force_keep"])
                item["tags"] = json.loads(item["tags"])
                item["related"] = json.loads(item["related"])
                results.append(item)
            return results

    async def list_nodes(
        self,
        namespace: str | None = None,
        status: str = "active",
    ) -> list[dict[str, Any]]:
        """List catalog entries filtered by namespace and status."""
        sql = "SELECT * FROM catalog_entries WHERE status = ?"
        params: list[Any] = [status]
        if namespace:
            sql += " AND namespace = ?"
            params.append(namespace)
        sql += " ORDER BY path ASC"

        db = await self._get_conn()
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                item = dict(row)
                item["force_keep"] = bool(item["force_keep"])
                item["tags"] = json.loads(item["tags"])
                item["related"] = json.loads(item["related"])
                results.append(item)
            return results
