"""Knowledge subsystem integration tools: query_knowledge and modify_knowledge."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from libhippo.runner.tools.base import BaseToolSuite, ToolExecutionError
from libhippo.runner.types import ToolDefinition
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.retrieval import KnowledgeDispatcher


class KnowledgeTools(BaseToolSuite):
    """Knowledge tools bridging 3-tier adaptive retrieval and Maker-Checker disk mutation."""

    def __init__(
        self,
        workspace_root: Path | None = None,
        sandbox: Any = None,
        project_manager: Any = None,
        *args: Any,
        store: KnowledgeStore | None = None,
        dispatcher: KnowledgeDispatcher | None = None,
        **kwargs: Any,
    ) -> None:
        if workspace_root is not None and sandbox is not None and project_manager is not None:
            super().__init__(
                workspace_root=workspace_root,
                sandbox=sandbox,
                project_manager=project_manager,
                *args,
                **kwargs,
            )
        else:
            self.workspace_root = workspace_root.resolve() if workspace_root else Path.cwd()
            self.sandbox = sandbox
            self.project_manager = project_manager
            self.event_callback = kwargs.get("event_callback")
            self._interactive_responses = {}
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

    async def read_knowledge(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        """Read line-addressed slice of a knowledge document by path."""
        if not self.store:
            raise ToolExecutionError("No knowledge store available.")

        await self.check_approval_if_needed(
            "read_knowledge",
            {"path": path, "start_line": start_line, "end_line": end_line},
        )

        norm = path.strip("/. ").replace("\\", "/")
        if norm.endswith(".md"):
            norm = norm[:-3]

        text: str | None = None
        try:
            phys, _ = self.store.mount_manager.resolve_virtual_path(norm)
            if phys.exists() and phys.is_file():
                text = phys.read_text(encoding="utf-8")
        except Exception:
            pass

        if text is None:
            # Gather candidates for fuzzy diagnostic suggestion
            candidates: set[str] = set()
            for mount in self.store.mount_manager.get_all_mounts():
                if mount.physical_path.exists():
                    for md_file in mount.physical_path.rglob("*.md"):
                        rel = md_file.relative_to(mount.physical_path).as_posix()
                        if rel.endswith(".md"):
                            rel = rel[:-3]
                        candidates.add(f"{mount.namespace_prefix}/{rel}")

            matches = difflib.get_close_matches(norm, sorted(candidates), n=3, cutoff=0.4)
            suggest = f" Did you mean '{matches[0]}'?" if matches else ""
            return {
                "status": "not_found",
                "path": norm,
                "message": f"Knowledge path '{norm}' not found.{suggest}",
            }

        lines = text.splitlines()
        total_lines = len(lines)
        s_line = max(1, start_line)
        e_line = min(total_lines, end_line) if end_line is not None else total_lines

        if s_line > total_lines:
            selected_content = ""
        else:
            selected = lines[s_line - 1 : e_line]
            selected_content = "\n".join(f"{idx}: {line}" for idx, line in enumerate(selected, start=s_line))

        return {
            "status": "success",
            "path": norm,
            "total_lines": total_lines,
            "start_line": s_line,
            "end_line": e_line,
            "content": selected_content,
        }

    async def list_knowledge(
        self,
        path: str = ".",
        max_depth: int = 2,
    ) -> str:
        """List knowledge hierarchy tree without curation or version checking."""
        if not self.store:
            raise ToolExecutionError("No knowledge store available.")
        await self.check_approval_if_needed("list_knowledge", {"path": path, "max_depth": max_depth})
        return await self.store.list_knowledge(path=path, max_depth=max_depth)

    async def search_knowledge(
        self,
        pattern: str = "*",
        path: str = ".",
        content_pattern: str | None = None,
    ) -> str:
        """Fast lexical and content regex search across knowledge documents."""
        if not self.store:
            raise ToolExecutionError("No knowledge store available.")
        await self.check_approval_if_needed(
            "search_knowledge",
            {"pattern": pattern, "path": path, "content_pattern": content_pattern},
        )
        return await self.store.search_knowledge(path=path, pattern=pattern, content_pattern=content_pattern)

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

            defs["list_knowledge"] = ToolDefinition(
                name="list_knowledge",
                description="List knowledge hierarchy tree structure without curation or version checks (pure directory listing).",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "default": ".", "description": "Knowledge namespace or subpath (e.g. '.', 'common', 'project')"},
                        "max_depth": {"type": "integer", "default": 2, "description": "Max recursion depth"},
                    },
                },
                handler=self.list_knowledge,
            )

            defs["search_knowledge"] = ToolDefinition(
                name="search_knowledge",
                description="Fast lexical path glob matching and optional regex search across knowledge documents (no LLM, no version check).",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "default": "*", "description": "Path glob pattern (e.g. '*react*', '*.md')"},
                        "path": {"type": "string", "default": ".", "description": "Knowledge namespace or directory to search"},
                        "content_pattern": {"type": "string", "description": "Optional regular expression to search within file contents"},
                    },
                },
                handler=self.search_knowledge,
            )

            defs["read_knowledge"] = ToolDefinition(
                name="read_knowledge",
                description="Read full or line-addressed slice of a knowledge document by path (e.g. 'common/react').",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Virtual knowledge path without .md extension (e.g. 'common/react')"},
                        "start_line": {"type": "integer", "default": 1, "description": "1-indexed start line"},
                        "end_line": {"type": "integer", "description": "1-indexed end line (optional)"},
                    },
                    "required": ["path"],
                },
                handler=self.read_knowledge,
            )

        return defs
