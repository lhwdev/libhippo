"""CuratorAgent: Write-protected Knowledge Draftsman with gpt-6-luna."""

from __future__ import annotations

import re
from typing import Any

from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import ChatCompletionClient

from libhippo.agents.base import BaseHippoAgent
from libhippo.agents.prompts import get_agent_system_prompt
from libhippo.models.knowledge import KnowledgeCandidate
from libhippo.models.llm import create_chat_client
from libhippo.tools.web import fetch_web, search_web


class CuratorAgent(AssistantAgent, BaseHippoAgent):
    """Knowledge Draftsman synthesizing technical specs into Hub-and-Leaf nodes.

    Equipped with search_web and fetch_web. Strictly write-protected (no modify_knowledge
    tools). Proposes candidates for review by CheckerAgent and VerifierAgent.
    """

    def __init__(
        self,
        name: str = "CuratorAgent",
        description: str = "Synthesizes external documentation into structured Hub-and-Leaf knowledge nodes.",
        model_client: ChatCompletionClient | None = None,
        tools: list[Any] | None = None,
        system_message: str | None = None,
        max_tool_iterations: int = 5,
    ) -> None:
        client = model_client or create_chat_client("curator")
        sys_msg = system_message or get_agent_system_prompt("curator")
        agent_tools = tools if tools is not None else [search_web, fetch_web]

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

    async def curate(
        self,
        topic_or_query: str,
        target_path: str | None = None,
        context: str = "",
    ) -> dict[str, Any]:
        """Synthesize technical specifications into a new Hub-and-Leaf candidate markdown."""
        prompt = (
            f"Research and draft a comprehensive, authoritative knowledge node for:\n"
            f"TOPIC / QUERY: {topic_or_query}\n"
        )
        if target_path:
            prompt += f"SUGGESTED_TARGET_PATH: {target_path}\n"
        if context:
            prompt += f"ADDITIONAL_CONTEXT:\n{context}\n"

        prompt += (
            "\nProduce the full GitHub-Flavored Markdown file including strict YAML frontmatter, "
            "Summary (Coarse View), and Detailed Rules & Edge Cases (Fine View)."
        )

        result = await self.run(task=prompt)
        last_message = result.messages[-1].content if result.messages else ""
        raw_text = last_message if isinstance(last_message, str) else str(last_message)

        draft = self.extract_markdown_draft(raw_text)
        path = target_path or self.infer_path(draft, topic_or_query)

        # Attempt to parse candidate
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

    async def revise(
        self,
        candidate_markdown: str,
        feedback: str,
        target_path: str = "common/revised.md",
    ) -> dict[str, Any]:
        """Revise an existing candidate markdown draft based on Checker or Verifier feedback."""
        prompt = (
            f"The following knowledge node draft failed audit or requires revision:\n\n"
            f"```markdown\n{candidate_markdown}\n```\n\n"
            f"AUDIT FEEDBACK & DIRECTIVES:\n{feedback}\n\n"
            f"Revise the markdown node to fix all reported issues while maintaining "
            f"strict YAML frontmatter and dual-view section layout."
        )

        result = await self.run(task=prompt)
        last_message = result.messages[-1].content if result.messages else ""
        raw_text = last_message if isinstance(last_message, str) else str(last_message)

        draft = self.extract_markdown_draft(raw_text)
        candidate = KnowledgeCandidate.from_markdown(target_path, draft)

        return {
            "path": target_path,
            "draft": draft,
            "title": candidate.frontmatter.title if candidate.frontmatter else "",
            "nature": candidate.frontmatter.nature if candidate.frontmatter else "foundation",
            "raw_response": raw_text,
        }

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
