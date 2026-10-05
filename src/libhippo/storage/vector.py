"""Local vector store for LibHippo knowledge nodes using ChromaDB."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api import ClientAPI

from libhippo.models.knowledge import KnowledgeCandidate, KnowledgeFrontmatter


@dataclass
class VectorSearchResult:
    """Individual vector search result with importance-weighted confidence."""

    path: str
    title: str
    namespace: str
    section: str
    snippet: str
    cosine_sim: float
    importance: float
    confidence: float


class VectorKnowledgeStore:
    """Manages dense chunk embeddings and similarity search using ChromaDB."""

    def __init__(
        self,
        persist_dir: Path | str | None = None,
        client: ClientAPI | None = None,
        collection_name: str = "libhippo_knowledge",
    ) -> None:
        self.collection_name = collection_name
        self.mutation_count: int = 0

        if client:
            self.client = client
        elif persist_dir:
            persist_path = Path(persist_dir)
            persist_path.mkdir(parents=True, exist_ok=True)
            self.client = chromadb.PersistentClient(path=str(persist_path))
        else:
            self.client = chromadb.EphemeralClient()

        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def reset(self) -> None:
        """Drop and recreate collection to purge tombstoned vectors and defragment HNSW."""
        import contextlib

        with contextlib.suppress(Exception):
            self.client.delete_collection(self.collection_name)
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self.mutation_count = 0


    def should_rebuild(self, threshold: int = 200) -> bool:
        """Check whether mutation count crossed the tombstone fragmentation threshold."""
        return self.mutation_count >= threshold

    def upsert(
        self,
        candidate: KnowledgeCandidate,
        summary: str = "",
        rules: str = "",
    ) -> None:
        """Upsert knowledge chunks (coarse summary and fine rules) into vector store."""
        # Remove any existing chunks for this path first
        self.delete(candidate.path)
        self.mutation_count += 1

        fm = candidate.frontmatter or KnowledgeFrontmatter(
            title=Path(candidate.path).stem,
            namespace="common",
        )

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []

        base_meta = {
            "path": candidate.path,
            "title": fm.title,
            "namespace": candidate.namespace,
            "importance": float(fm.importance),
            "force_keep": bool(fm.force_keep),
            "nature": fm.nature or "",
            "status": fm.status,
        }

        if summary.strip():
            ids.append(f"{candidate.path}#summary")
            documents.append(summary.strip())
            metadatas.append({**base_meta, "section": "summary"})

        if rules.strip():
            ids.append(f"{candidate.path}#rules")
            documents.append(rules.strip())
            metadatas.append({**base_meta, "section": "rules"})

        if not ids and candidate.body.strip():
            ids.append(f"{candidate.path}#full")
            documents.append(candidate.body.strip())
            metadatas.append({**base_meta, "section": "full"})

        if ids:
            self.collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )

    def delete(self, path: str) -> None:
        """Delete all chunks belonging to a given knowledge path."""
        try:
            self.collection.delete(where={"path": path})
            self.mutation_count += 1
        except Exception:  # noqa: BLE001, S110
            # No-op if path does not exist in collection
            pass

    def search(
        self,
        query: str,
        namespace: str | None = None,
        top_k: int = 5,
        alpha: float = 0.08,
    ) -> list[VectorSearchResult]:
        """Perform similarity search with importance-blended confidence scoring."""
        count = self.collection.count()
        if count == 0:
            return []

        where_clause = {"namespace": namespace} if namespace else None
        n_results = min(count, max(top_k * 2, 1))

        kwargs: dict[str, Any] = {
            "query_texts": [query],
            "n_results": n_results,
        }
        if where_clause:
            kwargs["where"] = where_clause

        res = self.collection.query(**kwargs)
        if not res or not res["ids"] or not res["ids"][0]:
            return []

        results: list[VectorSearchResult] = []
        best_per_path: dict[str, VectorSearchResult] = {}

        ids = res["ids"][0]
        distances = res["distances"][0] if res["distances"] else [0.0] * len(ids)
        documents = res["documents"][0] if res["documents"] else [""] * len(ids)
        metadatas = res["metadatas"][0] if res["metadatas"] else [{}] * len(ids)

        for chunk_id, dist, doc, meta in zip(ids, distances, documents, metadatas):
            # Chroma cosine distance is in [0, 2]; cosine similarity = max(0, 1 - dist)
            cosine_sim = max(0.0, min(1.0, 1.0 - float(dist)))
            importance = float(meta.get("importance", 0.5))

            # Importance-aware confidence formula: (1 - alpha) * cos_sim + alpha * importance
            confidence = round((1.0 - alpha) * cosine_sim + alpha * importance, 4)

            item = VectorSearchResult(
                path=str(meta.get("path", chunk_id.split("#")[0])),
                title=str(meta.get("title", "")),
                namespace=str(meta.get("namespace", "common")),
                section=str(meta.get("section", "full")),
                snippet=doc,
                cosine_sim=round(cosine_sim, 4),
                importance=importance,
                confidence=confidence,
            )

            # Deduplicate by path, keeping highest confidence chunk for each document
            if item.path not in best_per_path or item.confidence > best_per_path[item.path].confidence:
                best_per_path[item.path] = item

        results = list(best_per_path.values())
        results.sort(key=lambda x: x.confidence, reverse=True)
        return results[:top_k]
