"""Knowledge subsystem integration tools: query_knowledge, record_learning, complete_retrieval."""

from __future__ import annotations

import difflib
import json
import logging
from pathlib import Path
from typing import Any

from libhippo.orchestration.maker_checker import MakerCheckerOrchestrator
from libhippo.runner.memory import ContextMemory
from libhippo.runner.tools.base import BaseToolSuite, ToolExecutionError
from libhippo.runner.types import ToolDefinition
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.retrieval import KnowledgeDispatcher, RefineFeedback, apply_rejection

logger = logging.getLogger(__name__)


def _normalize_path(path: str) -> str:
    return path.strip("/. ").replace("\\", "/").removesuffix(".md")


class KnowledgeTools(BaseToolSuite):
    """Knowledge tools bridging 3-tier adaptive retrieval and learning queues."""

    memory: ContextMemory | None = None

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

    def _last_retrieval_output(self) -> Any:
        if not self.memory:
            return None
        for msg in reversed(self.memory.zone2_history):
            if msg.role == "tool" and msg.metadata.get("tool_name") in ("query_knowledge", "refine_knowledge"):
                return msg
        return None

    async def query_knowledge(
        self,
        query: str,
        effort: str = "medium",
        criticality: str = "preferred",
        feedback: RefineFeedback | None = None,
        rejected_path: str | None = None,
    ) -> dict[str, Any]:
        """Bridge query_knowledge into the harness using 3-tier adaptive retrieval.

        With `feedback`, refines the previous retrieval: supersedes its output in memory and
        excludes `rejected_path` (inferred from the previous output if omitted).
        """
        if not self.dispatcher and not self.store:
            raise ToolExecutionError("No knowledge store or dispatcher configured in this harness.")

        await self.check_approval_if_needed(
            "query_knowledge",
            {"query": query, "effort": effort, "criticality": criticality},
        )

        if feedback:
            prev = self._last_retrieval_output()
            if prev and not prev.content.startswith("[Superseded:"):
                if not rejected_path:
                    try:
                        parsed = json.loads(prev.content)
                        if isinstance(parsed, dict):
                            rejected_path = parsed.get("path")
                    except Exception:
                        pass
                rejected_desc = f" `{rejected_path}`" if rejected_path else ""
                if self.memory:
                    self.memory.replace_tool_output(
                        f"[Superseded: previous result{rejected_desc} was {feedback}; refined to '{query}' below]",
                        tool_call_id=prev.tool_call_id,
                    )
        if rejected_path:
            rejected_path = _normalize_path(rejected_path)

        if self.dispatcher:
            res = await self.dispatcher.query_knowledge(
                query=query,
                effort=effort,  # type: ignore
                criticality=criticality,  # type: ignore
                feedback=feedback,
                rejected_path=rejected_path,
            )
            hint = ""
            if res.status == "HIT" and res.confidence < 0.90:
                hint = f"\n\n[Hint: If this document is too broad or you need a deeper subtopic leaf, invoke refine_knowledge(feedback='too_broad', query='{query} <subtopic>').]"

            return {
                "status": res.status,
                "query": query,
                "path": res.path,
                "title": res.title,
                "content": f"{res.content}{hint}" if res.content else "",
                "confidence": res.confidence,
                "effort_tier": res.effort_tier,
                "criticality": res.criticality,
                "source": res.source,
            }
        elif self.store:
            nodes = await self.store.search(query=query, top_k=3 if not rejected_path else 8)
            if rejected_path:
                nodes = apply_rejection(nodes, feedback, rejected_path)[:3]
            return {
                "status": "success",
                "query": query,
                "retrieved_nodes": [
                    {"path": n.path, "content": n.content, "confidence": n.confidence}
                    for n in nodes
                ],
            }
        return {"status": "error", "message": "No knowledge store or dispatcher available"}

    async def refine_knowledge(
        self,
        feedback: RefineFeedback,
        query: str,
        rejected_path: str | None = None,
        effort: str = "medium",
        criticality: str = "preferred",
    ) -> dict[str, Any]:
        """Refine a prior query_knowledge result that was too broad or in the wrong direction."""
        return await self.query_knowledge(
            query=query,
            effort=effort,
            criticality=criticality,
            feedback=feedback,
            rejected_path=rejected_path,
        )

    async def complete_retrieval(
        self,
        outcome: str = "hit",
        path: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Finalize knowledge retrieval, and return to previous task.

        Outcomes:
        - "hit": Located an existing document (`path` required).
        - "create": No suitable document found; indicates a knowledge gap to create/draft (`path` optional).
        - "forgive": Knowledge not found.
        """
        norm = _normalize_path(path) if path else None

        # Extract last query from memory if available
        last_query = ""
        if self.memory:
            prev = self.memory.get_last_tool_output("query_knowledge")
            if prev and prev.content:
                last_query = prev.metadata.get("query", "")
                if not last_query:
                    try:
                        parsed = json.loads(prev.content)
                        if isinstance(parsed, dict):
                            last_query = parsed.get("query", "")
                    except Exception:
                        pass

        # Case 1: HIT
        if outcome == "hit":
            if not norm:
                return {"status": "error", "message": "Outcome 'hit' requires 'path' parameter."}
            if not self.store:
                raise ToolExecutionError("No knowledge store available.")

            content = await self.store.read_knowledge(norm)
            if not content:
                return {
                    "status": "error",
                    "message": f"Document '{norm}' could not be read or does not exist.",
                }

            if last_query and self.store:
                try:
                    await self.store.record_search_alias(norm, last_query)
                except Exception:
                    pass

            header_note = f"\nNote: {note}" if note else ""
            collapsed_payload = (
                f"[HIT; You found knowledge `{norm}`]{header_note}\n\n{content}"
            )
            if self.memory:
                self.memory.collapse_intermediate_turns(
                    anchor_tool_name="query_knowledge",
                    final_tool_content=collapsed_payload,
                )
            return {
                "status": "HIT",
                "path": norm,
                "message": f"Found corresponding knowledge and context compacted for {norm}.",
                "note": note,
            }

        # Case 2: CREATE (invokes Curator / MakerCheckerOrchestrator to research and draft node)
        if outcome == "create":
            topic_to_curate = note or norm or last_query
            curated_path: str | None = None
            curated_content: str | None = None

            if not self.dispatcher:
                raise ToolExecutionError("self.dispatcher == None")
            if not self.dispatcher.orchestrator:
                raise ToolExecutionError("self.dispatcher.orchestrator == None")
            
            try:
                gov_res = await self.dispatcher.orchestrator.curate_and_govern(
                    topic=topic_to_curate,
                    on_event=getattr(self.dispatcher, "on_event", None),
                )
                if gov_res.status == "COMMITTED":
                    curated_path = gov_res.path
                    if self.store:
                        curated_content = await self.store.read_knowledge(gov_res.path)
            except Exception as e:
                logger.warning(f"Orchestrated curation in complete_retrieval failed: {e}")

            if curated_content and curated_path:
                header_note = f"\nNote: {note}" if note else ""
                collapsed_payload = (
                    f"[HIT; Created knowledge on `{curated_path}`.]{header_note}\n\n{curated_content}"
                )
                if self.memory:
                    self.memory.collapse_intermediate_turns(
                        anchor_tool_name="query_knowledge",
                        final_tool_content=collapsed_payload,
                    )
                return {
                    "status": "HIT",
                    "path": curated_path,
                    "content": curated_content,
                    "message": f"Successfully created new knowledge `{curated_path}`, context compacted.",
                    "note": note,
                }

            # Fallback if no curator available or curation yielded no content
            target_desc = f" at '{norm}'" if norm else ""
            header_note = f"\nRationale: {note}" if note else ""
            collapsed_payload = (
                f"[MISS:GAP]{target_desc}{header_note}\n\n"
                "No adequate document was found in the knowledge base and automated curation is unavailable. 비상"
            )
            if self.memory:
                self.memory.collapse_intermediate_turns(
                    anchor_tool_name="query_knowledge",
                    final_tool_content=collapsed_payload,
                )
            return {
                "status": "MISS:GAP",
                "path": norm,
                "message": f"Recorded knowledge gap{target_desc}. Context compacted.",
                "note": note,
            }

        # Case 3: FORGIVE
        header_note = f"\nRationale: {note}" if note else ""
        collapsed_payload = f"[MISS:FALLBACK; No knowledge found.]{header_note}"
        if self.memory:
            self.memory.collapse_intermediate_turns(
                anchor_tool_name="query_knowledge",
                final_tool_content=collapsed_payload,
            )
        return {
            "status": "MISS:FALLBACK",
            "message": "Exploratory search concluded without knowledge; context compacted.",
            "note": note,
        }

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
                action=action, # type: ignore
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

        norm = _normalize_path(path)

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

            defs["refine_knowledge"] = ToolDefinition(
                name="refine_knowledge",
                description="Refine or specialize a prior query_knowledge match that was too broad, too narrow, or missing deep details.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "feedback": {
                            "type": "string",
                            "enum": ["too_broad", "too_narrow", "wrong_direction", "more_details"],
                            "description": "Why the previous knowledge match was insufficient",
                        },
                        "query": {"type": "string", "description": "Refined or specialized query targeting the specific child concept"},
                        "rejected_path": {"type": "string", "description": "Virtual path of the rejected candidate; defaults to the previous result's path"},
                    },
                    "required": ["feedback", "query"],
                },
                handler=self.refine_knowledge,
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

            defs["complete_retrieval"] = ToolDefinition(
                name="complete_retrieval",
                description="Finalize knowledge search with outcome (hit, create, or forgive).",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "outcome": {
                            "type": "string",
                            "enum": ["hit", "create_new", "forgive"],
                            "default": "hit",
                            "description": "Exploration conclusion: 'hit' (document found), 'create' (knowledge gap to draft), or 'forgive' (safe to proceed without knowledge)",
                        },
                        "path": {
                            "type": "string",
                            "description": "Virtual knowledge path to the located document (required if outcome='hit', optional if outcome='create')",
                        },
                        "note": {
                            "type": "string",
                            "description": "Concise note or rationale explaining the finding, gap, or reason for proceeding without knowledge",
                        },
                    },
                    "required": ["outcome"],
                },
                handler=self.complete_retrieval,
            )

        return defs
