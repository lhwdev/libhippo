"""BookKeeperAgent: Subagent with gpt-5-nano for zero-context, zero-resummarization technical retrieval."""

from __future__ import annotations

import re
from typing import Any

from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import ChatCompletionClient

from libhippo.agents.base import BaseHippoAgent
from libhippo.agents.prompts import get_agent_system_prompt
from libhippo.models.llm import create_chat_client
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.registry import ToolRegistry


class BookKeeperAgent(AssistantAgent, BaseHippoAgent):
    """Adaptive retrieval specialist running in a stateless zero-context sandbox.

    Expand queries, cross-reference knowledge documents, and extract verbatim
    rule snippets without semantic paraphrasing.
    """

    def __init__(
        self,
        name: str = "BookKeeperAgent",
        description: str = "Locates authoritative technical rules and returns verbatim snippets.",
        model_client: ChatCompletionClient | None = None,
        store: KnowledgeStore | None = None,
        tools: list[Any] | None = None,
        system_message: str | None = None,
        max_tool_iterations: int = 5,
    ) -> None:
        client = model_client or create_chat_client("book_keeper")
        sys_msg = system_message or get_agent_system_prompt("book_keeper")

        if tools is None and store is not None:
            reg = ToolRegistry(store=store)
            tools = [
                reg.get_search_knowledge_tool(),
                reg.get_read_knowledge_tool(),
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

    async def lookup(
        self,
        query: str,
        candidates: list[Any] | None = None,
        criticality: str = "preferred",
    ) -> dict[str, Any]:
        """Perform a stateless, zero-context lookup and parse the structured output."""
        task_prompt = f"QUERY: {query}\nCRITICALITY: {criticality}\n"
        if candidates:
            task_prompt += "INITIAL_CANDIDATES:\n"
            for c in candidates:
                path = getattr(c, "path", str(c))
                title = getattr(c, "title", "")
                conf = getattr(c, "confidence", "")
                task_prompt += f"- {path} ({title}, conf={conf})\n"

        result = await self.run(task=task_prompt)
        last_message = result.messages[-1].content if result.messages else ""
        text = last_message if isinstance(last_message, str) else str(last_message)

        return self.parse_output(text)

    @staticmethod
    def parse_output(text: str) -> dict[str, Any]:
        """Parse BookKeeperAgent's structured output format into a clean dictionary."""
        status_match = re.search(r"STATUS:\s*(\[[A-Z:]+\]|[A-Z:_]+)", text, re.IGNORECASE)
        status = status_match.group(1).strip() if status_match else "[MISS:FALLBACK]"
        if not status.startswith("[") and not status.endswith("]"):
            status = f"[{status}]"

        path_match = re.search(r"PATH:\s*([^\n\r]+)", text)
        path = path_match.group(1).strip() if path_match else None
        if path and path.upper() == "NONE":
            path = None

        conf_match = re.search(r"CONFIDENCE:\s*([0-9.]+)", text)
        confidence = float(conf_match.group(1)) if conf_match else 0.50

        title_match = re.search(r"TITLE:\s*([^\n\r]+)", text)
        title = title_match.group(1).strip() if title_match else ""
        if title.upper() == "NONE":
            title = ""

        snippet = ""
        snippet_match = re.search(r"SNIPPET:\s*```(?:markdown)?\s*\n(.*?)\n```", text, re.DOTALL)
        if snippet_match:
            snippet = snippet_match.group(1).strip()
        else:
            snip_alt = re.search(r"SNIPPET:\s*\n?(.*?)(?=RATIONALE:|$)", text, re.DOTALL)
            if snip_alt:
                snippet = snip_alt.group(1).strip()

        rat_match = re.search(r"RATIONALE:\s*([^\n\r]+)", text)
        rationale = rat_match.group(1).strip() if rat_match else ""

        return {
            "status": status,
            "path": path,
            "confidence": confidence,
            "title": title,
            "snippet": snippet,
            "rationale": rationale,
            "raw_response": text,
        }
