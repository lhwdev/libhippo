"""Knowledge subsystem integration tools: query_knowledge and modify_knowledge."""

from __future__ import annotations

from typing import Any

from libhippo.runner.tools.base import BaseToolSuite, ToolExecutionError
from libhippo.runner.types import ToolDefinition
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.retrieval import KnowledgeDispatcher


class KnowledgeTools(BaseToolSuite):
    """Knowledge tools bridging 3-tier adaptive retrieval and Maker-Checker disk mutation."""

    def __init__(
        self,
        *args: Any,
        store: KnowledgeStore | None = None,
        dispatcher: KnowledgeDispatcher | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.store = store or (dispatcher.store if dispatcher else None)
        self.dispatcher = dispatcher
        self.harvest_queue: list[dict[str, Any]] = []

    async def record_learning(
        self,
        topic: str,
        insight: str,
        scope: str = "project",
    ) -> dict[str, Any]:
        """Record an empirical learning or project convention for asynchronous harvesting."""
        entry = {
            "topic": topic,
            "insight": insight,
            "scope": scope,
        }
        self.harvest_queue.append(entry)
        return {
            "status": "queued",
            "topic": topic,
            "scope": scope,
            "message": "Learning queued for asynchronous sidecar audit and commitment.",
        }

    async def query_knowledge(
        self,
        query: str,
        effort: str = "medium",
        criticality: str = "preferred",
    ) -> dict[str, Any]:
        """Bridge query_knowledge into the harness using 3-tier adaptive retrieval."""
        if not self.dispatcher and not self.store:
            raise ToolExecutionError("No knowledge store or dispatcher configured in this harness.")

        await self.check_approval_if_needed(
            "query_knowledge",
            {"query": query, "effort": effort, "criticality": criticality},
        )

        if self.dispatcher:
            res = await self.dispatcher.query_knowledge(query=query, effort=effort, criticality=criticality)  # type: ignore
            return {
                "status": res.status,
                "query": query,
                "path": res.path,
                "title": res.title,
                "snippet": res.snippet,
                "confidence": res.confidence,
                "effort_tier": res.effort_tier,
                "criticality": res.criticality,
                "source": res.source,
            }
        elif self.store:
            nodes = await self.store.search(query=query, top_k=3)
            return {
                "status": "success",
                "query": query,
                "retrieved_nodes": [
                    {"path": n.path, "snippet": n.snippet, "confidence": n.confidence}
                    for n in nodes
                ],
            }
        return {"status": "error", "message": "No knowledge store or dispatcher available"}

    async def modify_knowledge(
        self,
        action: str,
        path: str,
        content: str = "",
        metadata: dict[str, Any] | None = None,
        extra_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Atomically commit markdown modifications, splits, merges, or deprecations to the knowledge base."""
        if not self.store:
            raise ToolExecutionError("No knowledge store available to execute modify_knowledge.")

        await self.check_approval_if_needed(
            "modify_knowledge",
            {"action": action, "path": path, "content_len": len(content)},
        )

        try:
            res = await self.store.modify_knowledge(
                action=action,
                path=path,
                content=content,
                metadata=metadata or {},
                extra_paths=extra_paths,
            )
            return {
                "status": "success",
                "action": action,
                "path": path,
                "result": res,
            }
        except Exception as e:
            raise ToolExecutionError(f"modify_knowledge failed: {e}")

    def get_tool_definitions(self) -> dict[str, ToolDefinition]:
        """Return ToolDefinition schemas for knowledge subsystem operations."""
        defs: dict[str, ToolDefinition] = {}

        defs["record_learning"] = ToolDefinition(
            name="record_learning",
            description="Flag an empirical learning, repository convention, or bug workaround to be asynchronously audited and stored in the knowledge base.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Short topic or title (e.g. 'React 19 form actions')"},
                    "insight": {"type": "string", "description": "Key rule, gotcha, or pattern discovered during task execution"},
                    "scope": {"type": "string", "enum": ["project", "common", "user"], "default": "project", "description": "Target knowledge scope"},
                },
                "required": ["topic", "insight"],
            },
            handler=self.record_learning,
        )

        if self.store or self.dispatcher:
            defs["query_knowledge"] = ToolDefinition(
                name="query_knowledge",
                description="Query the LibHippo hierarchical knowledge base with adaptive effort tiers.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Unambiguous, self-contained search query"},
                        "effort": {"type": "string", "enum": ["low", "medium", "high"], "default": "medium"},
                        "criticality": {"type": "string", "enum": ["mandatory", "preferred", "optional"], "default": "preferred"},
                    },
                    "required": ["query"],
                },
                handler=self.query_knowledge,
            )

        if self.store:
            defs["modify_knowledge"] = ToolDefinition(
                name="modify_knowledge",
                description="Commit markdown changes, splits, merges, or deprecations to knowledge mounts.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "update", "split", "merge", "purge", "deprecate", "revalidate"],
                            "description": "Mutation action to execute (use 'revalidate' to force check and update outdated knowledge)",
                        },
                        "path": {"type": "string", "description": "Target virtual path (e.g. project/api.md)"},
                        "content": {"type": "string", "description": "Markdown body content"},
                        "metadata": {"type": "object", "description": "Frontmatter metadata tags and attributes"},
                        "extra_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Additional paths involved in splits or merges",
                        },
                    },
                    "required": ["action", "path"],
                },
                handler=self.modify_knowledge,
            )

        return defs
