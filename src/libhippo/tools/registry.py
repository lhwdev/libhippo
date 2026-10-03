"""Tool registry creating callable tools for agents and AutoGen teams."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from libhippo.models.knowledge import (
    HubReference,
    KnowledgeCandidate,
    KnowledgeContext,
    SiblingReference,
)
from libhippo.storage.store import KnowledgeAction, KnowledgeStore, SectionType
from libhippo.tools.retrieval import (
    CriticalityTier,
    EffortTier,
    KnowledgeDispatcher,
    KnowledgeRetrievalResult,
)
from libhippo.tools.web import fetch_web, search_web


class ToolRegistry:
    """Creates bound agent tools for a given KnowledgeStore and KnowledgeDispatcher."""

    def __init__(
        self,
        store: KnowledgeStore,
        dispatcher: KnowledgeDispatcher | None = None,
        checker: Any | None = None,
    ) -> None:
        self.store = store
        self.dispatcher = dispatcher or KnowledgeDispatcher(store=store, checker=checker)
        self.checker = checker

    def get_search_knowledge_tool(self) -> Callable[..., Any]:
        """Tool: search_knowledge(query: str, namespace: str = None, top_k: int = 5)."""
        async def search_knowledge(
            query: str,
            namespace: str | None = None,
            top_k: int = 5,
        ) -> list[dict[str, Any]]:
            results = await self.store.search(query=query, namespace=namespace, top_k=top_k)
            return [
                {
                    "path": r.path,
                    "title": r.title,
                    "namespace": r.namespace,
                    "snippet": r.snippet,
                    "confidence": r.confidence,
                    "importance": r.importance,
                }
                for r in results
            ]

        return search_knowledge

    def get_read_knowledge_tool(self) -> Callable[..., Any]:
        """Tool: read_knowledge(file_path: str, section: 'summary'|'rules'|'full')."""
        async def read_knowledge(
            file_path: str,
            section: SectionType = "full",
        ) -> str:
            content = await self.store.read_section(file_path, section=section)
            if content is None:
                return f"[ERROR: Knowledge path '{file_path}' not found]"
            return content

        return read_knowledge

    def get_modify_knowledge_tool(self) -> Callable[..., Any]:
        """Tool: modify_knowledge(action, path, content, metadata, extra_paths)."""
        async def modify_knowledge(
            action: KnowledgeAction,
            path: str,
            content: str = "",
            metadata: dict[str, Any] | None = None,
            extra_paths: list[str] | None = None,
        ) -> dict[str, Any]:
            return await self.store.modify_knowledge(
                action=action,
                path=path,
                content=content,
                metadata=metadata,
                extra_paths=extra_paths,
            )

        return modify_knowledge

    def get_audit_knowledge_tool(self) -> Callable[..., Any]:
        """Tool: audit_knowledge(path, content, parent_path, sibling_paths)."""
        async def audit_knowledge(
            path: str,
            content: str,
            parent_path: str = "",
            sibling_paths: list[str] | None = None,
        ) -> dict[str, Any]:
            if not self.checker:
                from libhippo.agents.checker import CheckerAgent
                self.checker = CheckerAgent()

            candidate = KnowledgeCandidate.from_markdown(path, content)
            ctx = KnowledgeContext(
                parent=HubReference(path=parent_path) if parent_path else None,
                siblings=[SiblingReference(path=p) for p in (sibling_paths or [])],
            )
            report = await self.checker.check(candidate, ctx)
            return report.model_dump()

        return audit_knowledge

    def get_query_knowledge_tool(self) -> Callable[..., Any]:
        """Tool: query_knowledge(query, effort, criticality)."""
        async def query_knowledge(
            query: str,
            effort: EffortTier = "medium",
            criticality: CriticalityTier = "preferred",
        ) -> dict[str, Any]:
            result: KnowledgeRetrievalResult = await self.dispatcher.query_knowledge(
                query=query,
                effort=effort,
                criticality=criticality,
            )
            return result.model_dump()

        return query_knowledge

    def get_fetch_web_tool(self) -> Callable[..., Any]:
        return fetch_web

    def get_search_web_tool(self) -> Callable[..., Any]:
        return search_web
