"""CuratorAgent: Write-protected Knowledge Draftsman with gpt-6-luna."""

from __future__ import annotations

import re
from typing import Any, AsyncGenerator, Sequence

from autogen_agentchat.base import Response
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import BaseChatMessage, TextMessage, ToolCallExecutionEvent
from autogen_core import CancellationToken
from autogen_core.models import ChatCompletionClient

from libhippo.agents.base import BaseHippoAgent
from libhippo.agents.draftsman import KnowledgeDraftSession
from libhippo.agents.prompts import get_agent_system_prompt
from libhippo.models.knowledge import KnowledgeCandidate
from libhippo.models.llm import create_chat_client
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.web import fetch_web, search_web


class CuratorAgent(AssistantAgent, BaseHippoAgent):
    """Knowledge Draftsman synthesizing technical specs into Hub-and-Leaf nodes.

    Equipped with write_knowledge, read_knowledge, list_knowledge, search_knowledge,
    commit, commit_all, run_command, search_web, and fetch_web.
    Strictly write-protected from directly mutating the knowledge store.
    """

    def __init__(
        self,
        name: str = "CuratorAgent",
        description: str = "Synthesizes external documentation into structured Hub-and-Leaf knowledge nodes.",
        model_client: ChatCompletionClient | None = None,
        store: KnowledgeStore | None = None,
        sandbox: Any | None = None,
        tools: list[Any] | None = None,
        system_message: str | None = None,
        max_tool_iterations: int = 10,
        orchestrator: Any | None = None,
    ) -> None:
        client = model_client or create_chat_client("curator")
        sys_msg = system_message or get_agent_system_prompt("curator")
        self.store = store
        self.sandbox = sandbox
        self.orchestrator = orchestrator
        self.current_session: KnowledgeDraftSession | None = None

        agent_tools = tools if tools is not None else [
            self.write_knowledge,
            self.read_knowledge,
            self.list_knowledge,
            self.search_knowledge,
            self.commit,
            self.commit_all,
            self.run_command,
            search_web,
            fetch_web,
        ]

        AssistantAgent.__init__(
            self,
            name=name,
            model_client=client,
            tools=agent_tools,
            system_message=sys_msg,
            description=description,
            max_tool_iterations=max_tool_iterations,
        )
        BaseHippoAgent.__init__(self, name=name, description=description)

    async def on_messages_stream(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> AsyncGenerator[Any]:
        """Stream messages and stop immediately once knowledge draft is committed."""
        
        async for event in AssistantAgent.on_messages_stream(self, messages, cancellation_token):
            yield event
            if isinstance(event, ToolCallExecutionEvent):
                if self.current_session and self.current_session.is_committed and len(self.current_session.drafts) == 0:
                    committed_target = (
                        self.current_session.last_commit_result.path
                        if self.current_session.last_commit_result
                        else (self.current_session.active_path or "knowledge node")
                    )
                    yield Response(
                        chat_message=TextMessage(
                            content=f"Knowledge draft successfully committed to '{committed_target}'.",
                            source=self.name,
                        ),
                        inner_messages=[],
                    )
                    return

    def _get_current_session(self) -> KnowledgeDraftSession:
        if not self.current_session:
            self.current_session = KnowledgeDraftSession(
                store=self.store, sandbox=self.sandbox, orchestrator=self.orchestrator
            )
        return self.current_session

    async def write_knowledge(
        self,
        path: str,
        content: str,
        start_line: int | None = None,
        end_line: int | None = None,
        target: str | None = None,
    ) -> str:
        """Edit or replace a draft document. Returns immediate deterministic sanity check results."""
        return await self._get_current_session().write_knowledge(
            path=path, content=content, start_line=start_line, end_line=end_line, target=target
        )

    async def read_knowledge(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        """Read line-addressed slice of a draft document or existing store knowledge node."""
        return await self._get_current_session().read_knowledge(
            path=path, start_line=start_line, end_line=end_line
        )

    async def list_knowledge(self, path: str = ".", max_depth: int = 2) -> str:
        """Structural listing of knowledge namespaces and documents behaving like list_dir."""
        return await self._get_current_session().list_knowledge(path=path, max_depth=max_depth)

    async def search_knowledge(
        self,
        path: str,
        pattern: str = "*",
        content_pattern: str | None = None,
    ) -> str:
        """Fast lexical path glob matching and optional regex search across knowledge documents."""
        return await self._get_current_session().search_knowledge(path=path, pattern=pattern, content_pattern=content_pattern)

    async def commit(self, path: str) -> dict[str, Any]:
        """Validate and commit a single draft to Maker-Checker governance."""
        if not self.current_session:
            return {"status": "error", "message": "No active draft session."}
        return await self.current_session.commit(path=path)

    async def commit_all(self) -> dict[str, Any]:
        """Validate and commit all open drafts in the session to Maker-Checker governance."""
        if not self.current_session:
            return {"status": "error", "message": "No active draft session."}
        return await self.current_session.commit_all()

    async def run_command(self, command_line: str) -> dict[str, Any]:
        """Execute sandboxed terminal command to verify APIs or behavior."""
        return await self._get_current_session().run_command(command_line=command_line)

    async def curate(
        self,
        topic_or_query: str,
        target_path: str | None = None,
        context: str = "",
    ) -> dict[str, Any]:
        """Synthesize technical specifications into a new Hub-and-Leaf candidate markdown."""
        current_session = self._get_current_session()

        try:
            if target_path:
                norm_target = current_session._normalize_path(target_path)
                suggested_target_line = f"SUGGESTED_TARGET_PATH: {norm_target}\n"
            else:
                suggested_target_line = (
                    "TARGET_PATH: Determine an appropriate canonical, hierarchical snake_case path "
                    "under the relevant mount (e.g. common/<domain>/<topic>).\n"
                )
            prompt = (
                "Research and draft a comprehensive, authoritative knowledge document for `curate:USER_QUERY`.\n"
                f"<curate:USER_QUERY>{topic_or_query}</curate:USER_QUERY>\n"
                "\n"
                "<curate:CONCEPTUAL_SCOPE>\n"
                "The curated document is NOT necessarily a 1:1 mapping from the query.\n"
                "A knowledge document represents a durable, modular technical concept that can be searched from the query, "
                "not merely what the query literally requests (e.g. QUERY: `React useEffect` -> DOCUMENT: `React lifecycle hooks`).\n"
                "</curate:CONCEPTUAL_SCOPE>\n"
                "\n"
                f"{suggested_target_line}"
            )
            if context:
                prompt += f"ADDITIONAL_CONTEXT:\n{context}\n"

            prompt += (
                "\nFollow the curation workflow:\n"
                "1. Use list_knowledge / search_knowledge to check existing knowledge and avoid duplication.\n"
                "2. Use search_web and fetch_web to research official documentation.\n"
                "3. Use write_knowledge(path=..., content=...) to write the draft document.\n"
                "4. Once checks pass, call commit(path=...) to audit and save the draft to disk. If fails, modify.\n"
                "Document requirements: markdown with strict YAML frontmatter, with proper length."
            )

            result = await self.run(task=prompt)
            last_message = result.messages[-1].content if result.messages else ""
            raw_text = last_message if isinstance(last_message, str) else str(last_message)

            gov_result = getattr(self.current_session, "last_commit_result", None)

            draft = ""
            norm_target = current_session._normalize_path(target_path) if target_path else None
            if current_session.drafts:
                if norm_target and norm_target in current_session.drafts:
                    draft = current_session.drafts[norm_target].read_text(encoding="utf-8")
                    path = norm_target
                else:
                    path = current_session.active_path or next(iter(current_session.drafts.keys()))
                    draft = current_session.drafts[path].read_text(encoding="utf-8")
            else:
                draft = self.extract_markdown_draft(raw_text)
                path = target_path or self.infer_path(draft, topic_or_query, store=self.store)
                norm_p = current_session._normalize_path(path)
                await current_session.write_knowledge(norm_p, draft)
                path = norm_p

            candidate = KnowledgeCandidate.from_markdown(path, draft)
            title = candidate.frontmatter.title if candidate.frontmatter else topic_or_query
            nature = candidate.frontmatter.nature if candidate.frontmatter else "foundation"

            res = {
                "path": path,
                "draft": draft,
                "title": title,
                "nature": nature,
                "raw_response": raw_text,
            }
            if gov_result:
                res["gov_result"] = gov_result
            return res
        finally:
            if self.current_session:
                self.current_session.cleanup()
                self.current_session = None

    async def revise(
        self,
        candidate_markdown: str,
        feedback: str,
        target_path: str = "common/revised",
    ) -> dict[str, Any]:
        """Revise an existing candidate markdown draft based on Checker or Verifier feedback."""
        current_session = self._get_current_session()

        try:
            norm_path = current_session._normalize_path(target_path)
            draft_file = current_session.drafts_dir / f"{norm_path}.md"
            draft_file.parent.mkdir(parents=True, exist_ok=True)
            draft_file.write_text(candidate_markdown, encoding="utf-8")
            current_session.drafts[norm_path] = draft_file
            current_session.active_path = norm_path

            prompt = (
                f"The knowledge draft '{norm_path}' requires revision:\n\n"
                f"AUDIT FEEDBACK & DIRECTIVES:\n{feedback}\n\n"
                f"Use content/line replacements with `write_knowledge` to make minimal, surgical edits to fix reported issues.\n"
                f"When checks pass, call commit(path='{norm_path}')."
            )

            result = await self.run(task=prompt)
            last_message = result.messages[-1].content if result.messages else ""
            raw_text = last_message if isinstance(last_message, str) else str(last_message)

            gov_result = getattr(self.current_session, "last_commit_result", None)

            extracted_from_text = self.extract_markdown_draft(raw_text)
            if "---" in extracted_from_text and extracted_from_text != candidate_markdown:
                draft = extracted_from_text
                draft_file.write_text(draft, encoding="utf-8")
            elif norm_path in current_session.drafts:
                draft = current_session.drafts[norm_path].read_text(encoding="utf-8")
            else:
                draft = extracted_from_text

            candidate = KnowledgeCandidate.from_markdown(target_path, draft)

            res = {
                "path": target_path,
                "draft": draft,
                "title": candidate.frontmatter.title if candidate.frontmatter else "",
                "nature": candidate.frontmatter.nature if candidate.frontmatter else "foundation",
                "raw_response": raw_text,
            }
            if gov_result:
                res["gov_result"] = gov_result
            return res
        finally:
            if self.current_session:
                self.current_session.cleanup()
                self.current_session = None

    @staticmethod
    def extract_markdown_draft(text: str) -> str:
        """Extract markdown document containing YAML frontmatter from raw LLM text."""
        # Try finding markdown code block containing YAML frontmatter
        fence_match = re.search(r"```(?:markdown)?\s*\n(---.*?```)", text, re.DOTALL)
        if fence_match:
            # Strip trailing fence if captured
            content = fence_match.group(1).rstrip()
            if content.endswith("```"):
                content = content[:-3].rstrip()
            return content

        # Direct YAML frontmatter detection
        direct_match = re.search(r"(^---\s*\n.*?)(?:\Z|```)", text, re.DOTALL | re.MULTILINE)
        if direct_match:
            return direct_match.group(1).strip()

        return text.strip()

    @staticmethod
    def infer_path(draft: str, default_query: str, store: Any | None = None) -> str:
        """Infer namespaced virtual path from frontmatter or query following Hub-and-Leaf rules."""
        ns_match = re.search(r"^namespace:\s*[\"']?([a-zA-Z0-9_\-]+)[\"']?", draft, re.MULTILINE)
        namespace = ns_match.group(1).strip() if ns_match else "common"
        if namespace == "knowledge":
            namespace = "common"

        title_match = re.search(r"^title:\s*[\"']?([^\"'\n]+)[\"']?", draft, re.MULTILINE)
        raw_name = title_match.group(1).strip() if title_match else default_query

        # Strip conversational query filler words
        cleaned = re.sub(
            r"\b(specification|specifications|specs?|usage|guides?|overview|docs?|documentation|tutorials?|cheat_sheets?)\b",
            "",
            raw_name,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"[:,\-_()]+", " ", cleaned).strip()
        if not cleaned:
            cleaned = raw_name

        words = cleaned.split()
        if len(words) >= 2 and store:
            first_slug = re.sub(r"[^a-zA-Z0-9_]+", "_", words[0].lower()).strip("_")
            rest_slug = re.sub(r"[^a-zA-Z0-9_]+", "_", "_".join(words[1:]).lower()).strip("_")

            # Check if parent hub exists in store
            parent_candidate = f"{namespace}/{first_slug}"
            parent_exists = False
            try:
                phys, _ = store.mount_manager.resolve_virtual_path(parent_candidate)
                parent_exists = (phys.exists() and phys.is_file()) or phys.with_suffix(".md").exists()
            except Exception:
                pass

            if parent_exists and rest_slug:
                return f"{namespace}/{first_slug}/{rest_slug}"

        slug = re.sub(r"[^a-zA-Z0-9_]+", "_", cleaned.lower()).strip("_")
        return f"{namespace}/{slug}"
