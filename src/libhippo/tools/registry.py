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
        agent_manager: Any | None = None,
    ) -> None:
        self.store = store
        self.agent_manager = agent_manager
        if agent_manager is not None:
            self.checker = checker or agent_manager.checker
            self.dispatcher = dispatcher or agent_manager.create_dispatcher()
        else:
            self.checker = checker
            self.dispatcher = dispatcher

    def get_list_knowledge_tool(self) -> Callable[..., Any]:
        """Tool: list_knowledge(path: str = '.', max_depth: int = 2)."""
        async def list_knowledge(path: str = ".", max_depth: int = 2) -> str:
            return await self.store.list_knowledge(path=path, max_depth=max_depth)

        return list_knowledge

    def get_similarity_search_knowledge_tool(self) -> Callable[..., Any]:
        """{ query: string, namespace?: string = null, top_k: int = 5 }"""
        async def search_knowledge(
            query: str,
            namespace: str | None = None,
            top_k: int = 5,
            **kwargs: Any,
        ) -> Any:
            results = await self.store.search(query=query, namespace=namespace, top_k=top_k)
            return [
                {
                    "path": r.path,
                    "title": r.title,
                    "namespace": r.namespace,
                    "content": r.content,
                    "confidence": r.confidence,
                    "importance": r.importance,
                }
                for r in results
            ]
        return search_knowledge

    def get_exact_search_knowledge_tool(self) -> Callable[..., Any]:
        """{ path: string, pattern: string = "*", content_pattern?: string }"""
        async def search_knowledge(
            path: str = ".",
            pattern: str = "*",
            content_pattern: str | None = None,
            query: str | None = None,
        ) -> Any:
            effective_pattern = pattern if pattern != "*" else (query or "*")
            return await self.store.search_knowledge(
                path=path,
                pattern=effective_pattern,
                content_pattern=content_pattern,
            )

        return search_knowledge

    def get_read_knowledge_tool(self) -> Callable[..., Any]:
        """{ path: string, start_line: int = 1, end_line?: int }"""
        async def read_knowledge(
            path: str | None = None,
            start_line: int = 1,
            end_line: int | None = None,
            section: SectionType = "full",
        ) -> str:
            if not path:
                return "[ERROR: Knowledge path must be specified]"
            content = await self.store.read_section(path, section=section)
            if content is None:
                return f"[ERROR: Knowledge path '{path}' not found]"
            if start_line > 1 or end_line is not None:
                lines = content.splitlines()
                s = max(1, start_line)
                e = min(len(lines), end_line) if end_line is not None else len(lines)
                selected = lines[s - 1 : e]
                return "\n".join(f"{idx}: {line}" for idx, line in enumerate(selected, start=s))
            return content

        return read_knowledge

    def get_write_knowledge_tool(self) -> Callable[..., Any]:
        """Tool: write_knowledge(path, content, start_line=None, end_line=None, target=None)."""
        async def write_knowledge(
            path: str,
            content: str,
            start_line: int | None = None,
            end_line: int | None = None,
            target: str | None = None,
        ) -> str:
            try:
                phys = self.store.mount_manager.resolve_virtual_path(path)[0]
            except Exception:
                phys = self.store.root_dir / path.strip("/")
            current_text = phys.read_text(encoding="utf-8") if phys.exists() and phys.is_file() else ""
            if start_line is None and end_line is None and target is None:
                new_text = content
            elif target is not None:
                if target not in current_text:
                    return f"[ERROR: Target string not found in '{path}']"
                new_text = current_text.replace(target, content, 1)
            else:
                lines = current_text.splitlines()
                s_idx = max(0, (start_line or 1) - 1)
                e_idx = end_line if end_line is not None else len(lines)
                lines[s_idx:e_idx] = content.splitlines()
                new_text = "\n".join(lines) + ("\n" if current_text.endswith("\n") or not current_text else "")
            phys.parent.mkdir(parents=True, exist_ok=True)
            phys.write_text(new_text, encoding="utf-8")
            return f"Successfully updated '{path}' ({len(new_text.splitlines())} lines)."

        return write_knowledge

    def get_run_command_tool(self) -> Callable[..., Any]:
        """Tool: run_command(command_line: str)."""
        async def run_command(command_line: str) -> dict[str, Any]:
            import asyncio
            proc = await asyncio.create_subprocess_shell(
                command_line,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            return {
                "exit_code": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
            }

        return run_command

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
            dispatcher = self.dispatcher
            if not dispatcher:
                if self.agent_manager:
                    dispatcher = self.agent_manager.create_dispatcher()
                else:
                    dispatcher = KnowledgeDispatcher(store=self.store, checker=self.checker)
                self.dispatcher = dispatcher

            result: KnowledgeRetrievalResult = await dispatcher.query_knowledge(
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
