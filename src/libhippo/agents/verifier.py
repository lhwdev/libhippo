"""VerifierAgent: Knowledge Refactoring Authority and Escalation Arbitrator with gpt-6.1-sol."""

from __future__ import annotations

import re
from typing import Any

from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import ChatCompletionClient

from libhippo.agents.base import BaseHippoAgent
from libhippo.agents.prompts import get_agent_system_prompt
from libhippo.models.audit import JevAuditReport
from libhippo.models.knowledge import KnowledgeCandidate
from libhippo.models.llm import create_chat_client
from libhippo.storage.mount import ReadOnlyMountError
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.registry import ToolRegistry


class VerifierAgent(AssistantAgent, BaseHippoAgent):
    """Refactoring authority and sole reasoning agent equipped with modify_knowledge.

    Handles Maker-Checker escalations, Hub promotions, directory partitioning for
    oversized nodes, sibling coalescing for undersized nodes, and deprecation routing.
    """

    def __init__(
        self,
        name: str = "VerifierAgent",
        description: str = "Knowledge Refactoring Authority and Architectural Gatekeeper.",
        model_client: ChatCompletionClient | None = None,
        store: KnowledgeStore | None = None,
        tools: list[Any] | None = None,
        system_message: str | None = None,
        max_tool_iterations: int = 8,
    ) -> None:
        client = model_client or create_chat_client("verifier")
        sys_msg = system_message or get_agent_system_prompt("verifier")

        if tools is None and store is not None:
            reg = ToolRegistry(store=store)
            tools = [
                reg.get_modify_knowledge_tool(),
                reg.get_read_knowledge_tool(),
                reg.get_search_knowledge_tool(),
            ]

        AssistantAgent.__init__(
            self,
            name=name,
            model_client=client,
            tools=tools or [],
            system_message=sys_msg,
            description=description,
            max_tool_iterations=max_tool_iterations,
        )
        BaseHippoAgent.__init__(self, name=name, description=description)
        self.store = store

    async def resolve_escalation(
        self,
        candidate: KnowledgeCandidate,
        report: JevAuditReport,
        store: KnowledgeStore | None = None,
    ) -> dict[str, Any]:
        """Arbitrate an ESCALATE_REFACTOR audit report by planning and executing refactoring."""
        effective_store = store or self.store
        errors = report.schema_errors + report.content_errors
        diagnostics = "\n".join(f"- {d}" for d in errors) if errors else "None"
        prompt = (
            f"CheckerAgent has emitted an audit verdict for knowledge node '{candidate.path}':\n"
            f"VERDICT: {report.verdict}\n"
            f"SIZE STATUS: {report.size_status} (tokens: {report.effective_token_count})\n"
            f"TAXONOMY FIT: {report.taxonomy_fit}\n"
            f"DIAGNOSTICS:\n{diagnostics}\n\n"
            f"CANDIDATE CONTENT:\n```markdown\n{candidate.markdown}\n```\n\n"
            f"Please evaluate whether to:\n"
            f"1. Promote to parent hub and partition child leaves (if OVERSIZED).\n"
            f"2. Coalesce into sibling/parent (if UNDERSIZED or redundant).\n"
            f"3. Execute direct mutations via modify_knowledge or output refactoring plan.\n"
        )

        result = await self.run(task=prompt)
        last_message = result.messages[-1].content if result.messages else ""
        raw_text = last_message if isinstance(last_message, str) else str(last_message)

        parsed = self.parse_directive(raw_text)

        # If store is available and refactoring planned but not executed via tool,
        # we can verify or complete operations
        if effective_store and parsed.get("action") == "split" and parsed.get("children"):
            try:
                hub_path = parsed.get("parent_hub") or candidate.path
                await effective_store.modify_knowledge(
                    action="split",
                    path=hub_path,
                    extra_paths=[c["path"] for c in parsed["children"]],
                )
                parsed["status"] = "MUTATION_EXECUTED"
            except ReadOnlyMountError as e:
                parsed["status"] = "REVISE_REJECTED"
                parsed["error"] = f"ReadOnlyMountError: {e}"

        return parsed

    async def split_oversized_node(
        self,
        path: str,
        store: KnowledgeStore,
        hub_content: str,
        child_nodes: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Perform deterministic leaf promotion to hub and save partition child leaves."""
        child_paths: list[str] = []
        for child in child_nodes:
            c_path = child["path"]
            c_content = child["content"]
            await store.save_node(c_path, c_content)
            child_paths.append(c_path)

        # Update parent hub with child directory index
        await store.modify_knowledge(
            action="split",
            path=path,
            content=hub_content,
            extra_paths=child_paths,
        )

        return {
            "status": "success",
            "action": "split",
            "hub": path,
            "children": child_paths,
        }

    async def merge_nodes(
        self,
        target_path: str,
        extra_paths: list[str],
        content: str,
        store: KnowledgeStore,
        force: bool = False,
    ) -> dict[str, Any]:
        """Merge multiple sibling nodes into a single consolidated target node."""
        return await store.modify_knowledge(
            action="merge",
            path=target_path,
            content=content,
            extra_paths=extra_paths,
            force=force,
        )

    @staticmethod
    def parse_directive(text: str) -> dict[str, Any]:
        """Parse structured refactoring directive output from VerifierAgent."""
        status_match = re.search(r"STATUS:\s*([A-Z_]+)", text)
        status = status_match.group(1).strip() if status_match else "REFACTOR_PLANNED"

        hub_match = re.search(r"PARENT_HUB:\s*([^\n\r]+)", text)
        parent_hub = hub_match.group(1).strip() if hub_match else ""

        # Extract child node paths if present
        children: list[dict[str, str]] = []
        child_blocks = re.findall(r"-\s*PATH:\s*([^\n\r]+)(.*?)(?=-\s*PATH:|\n\s*RATIONALE:|$)", text, re.DOTALL)
        for c_path, c_details in child_blocks:
            c_path = c_path.strip()
            scope_m = re.search(r"SCOPE:\s*([^\n\r]+)", c_details)
            scope = scope_m.group(1).strip() if scope_m else ""
            children.append({"path": c_path, "scope": scope})

        rat_match = re.search(r"RATIONALE:\s*([^\n\r]+)", text)
        rationale = rat_match.group(1).strip() if rat_match else ""

        action = "split" if children else ("merge" if "merge" in text.lower() else "update")

        return {
            "status": status,
            "action": action,
            "parent_hub": parent_hub,
            "children": children,
            "rationale": rationale,
            "raw_response": text,
        }
