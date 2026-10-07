"""Local vector store for LibHippo knowledge nodes using ChromaDB."""

from __future__ import annotations

import re
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
    content: str
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
        # Capture any existing search alias chunk before deleting
        alias_id = f"{candidate.path}#search_alias"
        existing_alias = None
        try:
            res = self.collection.get(ids=[alias_id], include=["embeddings", "metadatas", "documents"])
            if res and res["ids"] and len(res.get("embeddings") or []) > 0:
                existing_alias = {
                    "embeddings": res["embeddings"],
                    "metadatas": res["metadatas"],
                    "documents": res["documents"],
                }
        except Exception:
            pass

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
            "tags": ", ".join(fm.tags) if fm.tags else "",
        }

        if fm.title.strip():
            ids.append(f"{candidate.path}#title")
            documents.append(fm.title.strip())
            metadatas.append({**base_meta, "section": "title"})

        if fm.tags:
            ids.append(f"{candidate.path}#keywords")
            documents.append(f"Title: {fm.title}\nKeywords: {', '.join(fm.tags)}")
            metadatas.append({**base_meta, "section": "keywords"})
            for i, tag in enumerate(fm.tags):
                tag_clean = tag.strip()
                if tag_clean:
                    ids.append(f"{candidate.path}#tag_{i}")
                    documents.append(tag_clean)
                    metadatas.append({**base_meta, "section": "tag"})

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

        if existing_alias and existing_alias.get("embeddings"):
            try:
                self.collection.upsert(
                    ids=[alias_id],
                    embeddings=existing_alias["embeddings"],
                    documents=existing_alias.get("documents") or ["Search Alias Centroid"],
                    metadatas=existing_alias.get("metadatas") or [{"path": candidate.path, "section": "search_alias"}],
                )
            except Exception:
                pass

    def update_alias_centroid(
        self,
        path: str,
        query: str,
        decay: float = 0.80,
    ) -> None:
        """Update the decayed search alias vector centroid for a document in ChromaDB."""
        clean_q = query.strip()
        if not clean_q:
            return

        embed_fn = getattr(self.collection, "_embedding_function", None)
        if not embed_fn:
            return

        try:
            q_emb_raw = embed_fn([clean_q])
            if not q_emb_raw or not q_emb_raw[0]:
                return
        except Exception:
            return

        import numpy as np

        q_emb = np.array(q_emb_raw[0], dtype=float)
        alias_id = f"{path}#search_alias"

        try:
            existing = self.collection.get(ids=[alias_id], include=["embeddings", "metadatas"])
        except Exception:
            existing = None

        if existing and existing.get("ids") and len(existing.get("embeddings") or []) > 0:
            old_emb = np.array(existing["embeddings"][0], dtype=float)
            new_emb = decay * old_emb + (1.0 - decay) * q_emb
            norm = np.linalg.norm(new_emb)
            new_emb = (new_emb / norm if norm > 0 else new_emb).tolist()
            hit_count = (existing["metadatas"][0] if existing.get("metadatas") else {}).get("hit_count", 1) + 1
        else:
            norm = np.linalg.norm(q_emb)
            new_emb = (q_emb / norm if norm > 0 else q_emb).tolist()
            hit_count = 1

        self.collection.upsert(
            ids=[alias_id],
            embeddings=[new_emb],
            documents=[f"Search Alias Centroid: {clean_q}"],
            metadatas=[{
                "path": path,
                "section": "search_alias",
                "hit_count": hit_count,
            }],
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
        w_content: float = 0.60,
        w_title: float = 0.25,
        w_tag: float = 0.15,
    ) -> list[VectorSearchResult]:
        """Perform similarity search with weighted sum of title, tag, and content cosine similarities."""
        count = self.collection.count()
        if count == 0:
            return []

        where_clause = {"namespace": namespace} if namespace else None
        n_results = min(count, max(top_k * 6, 12))

        kwargs: dict[str, Any] = {
            "query_texts": [query],
            "n_results": n_results,
        }
        if where_clause:
            kwargs["where"] = where_clause

        res = self.collection.query(**kwargs)
        if not res or not res["ids"] or not res["ids"][0]:
            return []

        ids = res["ids"][0]
        distances = res["distances"][0] if res["distances"] else [0.0] * len(ids)
        documents = res["documents"][0] if res["documents"] else [""] * len(ids)
        metadatas = res["metadatas"][0] if res["metadatas"] else [{}] * len(ids)

        # Aggregate vector matches by document path
        path_matches: dict[str, dict[str, Any]] = {}

        for chunk_id, dist, doc, meta in zip(ids, distances, documents, metadatas):
            cosine_sim = max(0.0, min(1.0, 1.0 - float(dist)))
            path = str(meta.get("path", chunk_id.split("#")[0]))
            section = str(meta.get("section", "full"))

            if path not in path_matches:
                path_matches[path] = {
                    "title": str(meta.get("title", "")),
                    "namespace": str(meta.get("namespace", "common")),
                    "importance": float(meta.get("importance", 0.5)),
                    "title_sim": None,
                    "tag_sim": None,
                    "content_sim": None,
                    "content_doc": "",
                    "content_section": "full",
                    "best_sim": -1.0,
                    "best_doc": doc,
                    "best_section": section,
                }

            entry = path_matches[path]
            if cosine_sim > entry["best_sim"]:
                entry["best_sim"] = cosine_sim
                entry["best_doc"] = doc
                entry["best_section"] = section

            if section == "title":
                entry["title_sim"] = max(entry["title_sim"] or 0.0, cosine_sim)
            elif section in ("tag", "keywords", "search_alias"):
                entry["tag_sim"] = max(entry["tag_sim"] or 0.0, cosine_sim)
            elif section in ("summary", "rules", "full"):
                if entry["content_sim"] is None or cosine_sim > entry["content_sim"]:
                    entry["content_sim"] = cosine_sim
                    entry["content_doc"] = doc
                    entry["content_section"] = section

        results: list[VectorSearchResult] = []
        for path, data in path_matches.items():
            content_sim = data["content_sim"]
            title_sim = data["title_sim"]
            tag_sim = data["tag_sim"]

            # Weighted sum over vector-matched components (title, tag, content)
            active_weights = 0.0
            weighted_score = 0.0

            if content_sim is not None:
                weighted_score += w_content * content_sim
                active_weights += w_content

            if title_sim is not None:
                weighted_score += w_title * title_sim
                active_weights += w_title

            if tag_sim is not None:
                weighted_score += w_tag * tag_sim
                active_weights += w_tag

            combined_sim = (weighted_score / active_weights) if active_weights > 0 else (data["best_sim"] if data["best_sim"] >= 0 else 0.0)
            if title_sim is not None and title_sim >= 0.85:
                combined_sim = max(combined_sim, title_sim * 0.95)
            if tag_sim is not None and tag_sim >= 0.85:
                combined_sim = max(combined_sim, tag_sim * 0.90)

            importance = data["importance"]
            confidence = min(1.0, round((1.0 - alpha) * combined_sim + alpha * importance, 4))

            chosen_doc = data["content_doc"] or data["best_doc"]
            chosen_section = data["content_section"] if data["content_doc"] else data["best_section"]

            results.append(
                VectorSearchResult(
                    path=path,
                    title=data["title"],
                    namespace=data["namespace"],
                    section=chosen_section,
                    content=chosen_doc,
                    cosine_sim=round(combined_sim, 4),
                    importance=importance,
                    confidence=confidence,
                )
            )

        results.sort(key=lambda x: x.confidence, reverse=True)
        return results[:top_k]
