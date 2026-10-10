"""VerifierAgent: Knowledge Refactoring Authority and Escalation Arbitrator."""

from __future__ import annotations

import json
import re
from typing import Any

from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import ChatCompletionClient
from pydantic import BaseModel, ConfigDict, Field

from libhippo.agents.base import BaseHippoAgent
from libhippo.agents.prompts import get_agent_system_prompt
from libhippo.models.audit import JevAuditReport
from libhippo.models.knowledge import KnowledgeCandidate
from libhippo.models.llm import create_chat_client
from libhippo.storage.mount import ReadOnlyMountError
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.registry import ToolRegistry


class VerifierChildNode(BaseModel):
    """Child node specification for partition or split refactoring."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Knowledge path of the child node")
    scope: str = Field(default="", description="Scope, title, or responsibility of the child node")
    content: str = Field(default="", description="Optional markdown content of the child node")


class VerifierDirective(BaseModel):
    """Structured response format for Verifier escalation resolution."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(
        default="REFACTOR_PLANNED",
        description="Resolution status: MUTATION_EXECUTED, REFACTOR_PLANNED, APPROVED, REJECTED",
    )
    action: str = Field(
        default="update",
        description="Refactoring action: split, merge, update, deprecate",
    )
    parent_hub: str | None = Field(default=None, description="Path to parent hub if promoting or splitting")
    children: list[VerifierChildNode] = Field(default_factory=list, description="Child nodes for split")
    rationale: str = Field(default="", description="Architectural rationale for refactoring decision")



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
                reg.get_list_knowledge_tool(),
                reg.get_exact_search_knowledge_tool(),
                reg.get_write_knowledge_tool(),
                reg.get_run_command_tool(),
            ]

        AssistantAgent.__init__(
            self,
            name=name,
            model_client=client,
            tools=tools or [],
            system_message=sys_msg,
            description=description,
            max_tool_iterations=max_tool_iterations,
            output_content_type=VerifierDirective,
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
            f"<audit>\nCheckerAgent has emitted an audit verdict for knowledge node '{candidate.path}':\n"
            "<audit:verdict>\n"
            f"VERDICT: {report.verdict}\n"
            f"SIZE STATUS: {report.size_status} (tokens: {report.effective_token_count})\n"
            f"TAXONOMY FIT: {report.taxonomy_fit}\n"
            f"DIAGNOSTICS:\n{diagnostics}\n\n"
            f"<audit:CANDIDATE_CONTENT>\n{candidate.markdown}\n</audit:CANDIDATE_CONTENT>\n</audit:verdict>\n\n"
            f"Please evaluate whether to:\n"
            f"1. Promote to parent hub and partition child leaves (if OVERSIZED).\n"
            f"2. Coalesce into sibling/parent (if UNDERSIZED or redundant).\n"
            f"3. Execute direct mutations via modify_knowledge or output refactoring plan.\n</audit>\n"
        )

        result = await self.run(task=prompt)
        last_message = result.messages[-1] if result.messages else None

        if hasattr(last_message, "content") and isinstance(last_message.content, VerifierDirective):
            obj = last_message.content
            parsed = {
                "status": obj.status,
                "action": obj.action,
                "parent_hub": obj.parent_hub or "",
                "children": [c.model_dump() if hasattr(c, "model_dump") else c for c in obj.children],
                "rationale": obj.rationale,
                "raw_response": obj.model_dump_json(),
            }
        else:
            raw_text = getattr(last_message, "content", "") if last_message else ""
            raw_text = raw_text if isinstance(raw_text, str) else str(raw_text)
            parsed = self.parse_directive(raw_text)

        # If store is available and refactoring planned but not executed via tool,
        # we can verify or complete operations
        if effective_store and parsed.get("action") == "split" and parsed.get("children"):
            try:
                hub_path = parsed.get("parent_hub") or candidate.path
                await effective_store.modify_knowledge(
                    action="split",
                    path=hub_path,
                    extra_paths=[c["path"] if isinstance(c, dict) else c.path for c in parsed["children"]],
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
        child_nodes: list[dict[str, str]] | list[VerifierChildNode],
    ) -> dict[str, Any]:
        """Perform deterministic leaf promotion to hub and save partition child leaves."""
        child_paths: list[str] = []
        for child in child_nodes:
            c_path = child["path"] if isinstance(child, dict) else child.path
            c_content = child.get("content", "") if isinstance(child, dict) else child.content
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
        trimmed = text.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                data = json.loads(trimmed)
                return {
                    "status": data.get("status", "REFACTOR_PLANNED"),
                    "action": data.get("action", "update"),
                    "parent_hub": data.get("parent_hub") or "",
                    "children": data.get("children", []),
                    "rationale": data.get("rationale", ""),
                    "raw_response": text,
                }
            except Exception:
                pass

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
