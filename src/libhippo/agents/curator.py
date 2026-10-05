"""CuratorAgent: Write-protected Knowledge Draftsman with gpt-6-luna."""

from __future__ import annotations

import re
from typing import Any

from autogen_agentchat.agents import AssistantAgent
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
    ) -> None:
        client = model_client or create_chat_client("curator")
        sys_msg = system_message or get_agent_system_prompt("curator")
        self.store = store
        self.sandbox = sandbox
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

    async def write_knowledge(
        self,
        content: str,
        path: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        target: str | None = None,
    ) -> str:
        """Edit or replace a draft document. Returns immediate deterministic sanity check results."""
        if not self.current_session:
            self.current_session = KnowledgeDraftSession(store=self.store, sandbox=self.sandbox)
        return await self.current_session.write_knowledge(
            content=content, path=path, start_line=start_line, end_line=end_line, target=target
        )

    async def read_knowledge(
        self,
        path: str | None = None,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        """Read line-addressed slice of a draft document or existing store knowledge node."""
        if not self.current_session:
            self.current_session = KnowledgeDraftSession(store=self.store, sandbox=self.sandbox)
        return await self.current_session.read_knowledge(
            path=path, start_line=start_line, end_line=end_line
        )

    async def list_knowledge(self, path: str = ".", max_depth: int = 2) -> str:
        """Structural listing of knowledge namespaces and documents behaving like list_dir."""
        if not self.current_session:
            self.current_session = KnowledgeDraftSession(store=self.store, sandbox=self.sandbox)
        return await self.current_session.list_knowledge(path=path, max_depth=max_depth)

    async def search_knowledge(
        self,
        pattern: str = "*",
        path: str = ".",
        content_pattern: str | None = None,
    ) -> str:
        """Fast lexical path glob matching and optional regex search across knowledge documents."""
        if not self.current_session:
            self.current_session = KnowledgeDraftSession(store=self.store, sandbox=self.sandbox)
        return await self.current_session.search_knowledge(
            pattern=pattern, path=path, content_pattern=content_pattern
        )

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
        if not self.current_session:
            self.current_session = KnowledgeDraftSession(store=self.store, sandbox=self.sandbox)
        return await self.current_session.run_command(command_line=command_line)

    async def curate(
        self,
        topic_or_query: str,
        target_path: str | None = None,
        context: str = "",
    ) -> dict[str, Any]:
        """Synthesize technical specifications into a new Hub-and-Leaf candidate markdown."""
        self.current_session = KnowledgeDraftSession(store=self.store, sandbox=self.sandbox)
        try:
            prompt = (
                f"Research and draft a comprehensive, authoritative knowledge node for:\n"
                f"TOPIC / QUERY: {topic_or_query}\n"
            )
            if target_path:
                prompt += f"SUGGESTED_TARGET_PATH: {target_path}\n"
            if context:
                prompt += f"ADDITIONAL_CONTEXT:\n{context}\n"

            prompt += (
                "\nFollow the curation workflow:\n"
                "1. Use list_knowledge / search_knowledge to check existing knowledge and avoid duplication.\n"
                "2. Use search_web and fetch_web to research official documentation.\n"
                "3. Use write_knowledge(path=..., content=...) to write the draft document. "
                "The tool will immediately return deterministic sanity check results.\n"
                "4. Once checks pass, call commit(path=...) to submit the draft for Maker-Checker review.\n"
                "Document requirements: strict YAML frontmatter (title, namespace, version, nature, source), "
                "dual-view (Summary coarse view, Detailed Rules fine view), 500-1000 tokens."
            )

            result = await self.run(task=prompt)
            last_message = result.messages[-1].content if result.messages else ""
            raw_text = last_message if isinstance(last_message, str) else str(last_message)

            draft = ""
            norm_target = self.current_session._normalize_path(target_path) if target_path else None
            if self.current_session.drafts:
                if norm_target and norm_target in self.current_session.drafts:
                    draft = self.current_session.drafts[norm_target].read_text(encoding="utf-8")
                    path = norm_target
                else:
                    path = self.current_session.active_path or next(iter(self.current_session.drafts.keys()))
                    draft = self.current_session.drafts[path].read_text(encoding="utf-8")
            else:
                draft = self.extract_markdown_draft(raw_text)
                path = target_path or self.infer_path(draft, topic_or_query)
                norm_p = self.current_session._normalize_path(path)
                await self.current_session.write_knowledge(draft, path=norm_p)
                path = norm_p

            candidate = KnowledgeCandidate.from_markdown(path, draft)
            title = candidate.frontmatter.title if candidate.frontmatter else topic_or_query
            nature = candidate.frontmatter.nature if candidate.frontmatter else "foundation"

            return {
                "path": path,
                "draft": draft,
                "title": title,
                "nature": nature,
                "raw_response": raw_text,
            }
        finally:
            if self.current_session:
                self.current_session.cleanup()
                self.current_session = None

    async def revise(
        self,
        candidate_markdown: str,
        feedback: str,
        target_path: str = "common/revised.md",
    ) -> dict[str, Any]:
        """Revise an existing candidate markdown draft based on Checker or Verifier feedback."""
        self.current_session = KnowledgeDraftSession(store=self.store, sandbox=self.sandbox)
        try:
            norm_path = self.current_session._normalize_path(target_path)
            draft_file = self.current_session.drafts_dir / norm_path
            draft_file.parent.mkdir(parents=True, exist_ok=True)
            draft_file.write_text(candidate_markdown, encoding="utf-8")
            self.current_session.drafts[norm_path] = draft_file
            self.current_session.active_path = norm_path

            prompt = (
                f"The knowledge node draft '{norm_path}' requires revision:\n\n"
                f"AUDIT FEEDBACK & DIRECTIVES:\n{feedback}\n\n"
                f"Draft content has been pre-loaded into draft session '{norm_path}'.\n"
                f"Use write_knowledge(path='{norm_path}', target='...', content='...') or line replacements "
                f"to make minimal, surgical edits to fix reported issues. Do NOT rewrite the entire file unless necessary.\n"
                f"When checks pass, call commit(path='{norm_path}')."
            )

            result = await self.run(task=prompt)
            last_message = result.messages[-1].content if result.messages else ""
            raw_text = last_message if isinstance(last_message, str) else str(last_message)

            extracted_from_text = self.extract_markdown_draft(raw_text)
            if "---" in extracted_from_text and extracted_from_text != candidate_markdown:
                draft = extracted_from_text
                draft_file.write_text(draft, encoding="utf-8")
            elif norm_path in self.current_session.drafts:
                draft = self.current_session.drafts[norm_path].read_text(encoding="utf-8")
            else:
                draft = extracted_from_text

            candidate = KnowledgeCandidate.from_markdown(target_path, draft)

            return {
                "path": target_path,
                "draft": draft,
                "title": candidate.frontmatter.title if candidate.frontmatter else "",
                "nature": candidate.frontmatter.nature if candidate.frontmatter else "foundation",
                "raw_response": raw_text,
            }
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
    def infer_path(draft: str, default_query: str) -> str:
        """Infer namespaced virtual path from frontmatter or query."""
        ns_match = re.search(r"^namespace:\s*[\"']?([a-zA-Z0-9_\-]+)[\"']?", draft, re.MULTILINE)
        namespace = ns_match.group(1).strip() if ns_match else "common"

        title_match = re.search(r"^title:\s*[\"']?([^\"'\n]+)[\"']?", draft, re.MULTILINE)
        raw_name = title_match.group(1).strip() if title_match else default_query
        slug = re.sub(r"[^a-zA-Z0-9_]+", "_", raw_name.lower()).strip("_")

        return f"{namespace}/{slug}.md"
