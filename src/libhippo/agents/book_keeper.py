"""BookKeeperAgent: Subagent with gpt-5-nano for zero-context, zero-resummarization technical retrieval."""

from __future__ import annotations

import json
import re
from typing import Any

from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import ChatCompletionClient
from pydantic import BaseModel, ConfigDict, Field

from libhippo.agents.base import BaseHippoAgent
from libhippo.agents.prompts import get_agent_system_prompt
from libhippo.models.llm import create_chat_client
from libhippo.storage.store import KnowledgeStore


class BookKeeperLookupOutput(BaseModel):
    """Structured response format for BookKeeper technical retrieval."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(
        default="[MISS:FALLBACK]",
        description="Retrieval status: [HIT], [MISS:STALE], [MISS:GAP], [MISS:FALLBACK]",
    )
    path: str | None = Field(default=None, description="Direct matching knowledge path if found")
    confidence: float = Field(default=0.5, description="Confidence score between 0.0 and 1.0")
    title: str = Field(default="", description="Title of the matched knowledge document")
    keywords: list[str] = Field(
        default_factory=list,
        description="Key search keywords and phrases connecting query to document",
    )
    rationale: str = Field(default="", description="Concise explanation for hit or miss")


class BookKeeperAgent(AssistantAgent, BaseHippoAgent):
    """Adaptive retrieval specialist running in a stateless zero-context sandbox.

    Expand queries, cross-reference knowledge documents, and extract relevant search
    keywords for confidence boosting.
    """

    def __init__(
        self,
        name: str = "BookKeeperAgent",
        description: str = "Locates authoritative technical rules and extracts search keywords for confidence boosting.",
        model_client: ChatCompletionClient | None = None,
        store: KnowledgeStore | None = None,
        tools: list[Any] | None = None,
        system_message: str | None = None,
        max_tool_iterations: int = 5,
    ) -> None:
        client = model_client or create_chat_client("book_keeper")
        sys_msg = system_message or get_agent_system_prompt("book_keeper")

        if tools is None and store is not None:
            from libhippo.tools.registry import ToolRegistry

            reg = ToolRegistry(store=store)
            tools = [
                reg.get_similarity_search_knowledge_tool(),
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
            output_content_type=BookKeeperLookupOutput,
        )
        BaseHippoAgent.__init__(self, name=name, description=description)
        self.store = store

    async def lookup(
        self,
        query: str,
        candidates: list[Any],
        criticality: str = "preferred",
    ) -> dict[str, Any]:
        """Perform a stateless, zero-context lookup and parse the structured output."""
        if not candidates:
            return {
                "status": "[MISS:FALLBACK]",
                "path": None,
                "confidence": 0.0,
                "title": "",
                "keywords": [],
                "rationale": "No initial candidates provided.",
                "raw_response": "",
            }

        task_prompt = f"QUERY: {query}\nCRITICALITY: {criticality}\n"
        task_prompt += "INITIAL_CANDIDATES:\n"
        for c in candidates:
            path = getattr(c, "path", str(c))
            title = getattr(c, "title", "")
            conf = getattr(c, "confidence", "")
            task_prompt += f"- {path} ({title}, conf={conf})\n"

        result = await self.run(task=task_prompt)
        last_message = result.messages[-1] if result.messages else None

        if hasattr(last_message, "content") and isinstance(last_message.content, BookKeeperLookupOutput):
            obj = last_message.content
            return {
                "status": obj.status,
                "path": obj.path,
                "confidence": obj.confidence,
                "title": obj.title,
                "keywords": obj.keywords,
                "rationale": obj.rationale,
                "raw_response": obj.model_dump_json(),
            }

        text = getattr(last_message, "content", "") if last_message else ""
        text = text if isinstance(text, str) else str(text)
        return self.parse_output(text)

    @staticmethod
    def parse_output(text: str) -> dict[str, Any]:
        """Parse BookKeeperAgent's structured output format into a clean dictionary."""
        trimmed = text.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                data = json.loads(trimmed)
                raw_kw = data.get("keywords", [])
                if isinstance(raw_kw, str):
                    raw_kw = [k.strip() for k in raw_kw.split(",") if k.strip()]
                return {
                    "status": data.get("status", "[MISS:FALLBACK]"),
                    "path": data.get("path"),
                    "confidence": float(data.get("confidence", 0.5)),
                    "title": data.get("title", ""),
                    "keywords": raw_kw,
                    "rationale": data.get("rationale", ""),
                    "raw_response": text,
                }
            except Exception:
                pass

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

        keywords = []
        kw_match = re.search(r"KEYWORDS:\s*([^\n\r]+)", text)
        if kw_match:
            keywords = [k.strip() for k in kw_match.group(1).split(",") if k.strip()]

        rat_match = re.search(r"RATIONALE:\s*([^\n\r]+)", text)
        rationale = rat_match.group(1).strip() if rat_match else ""

        return {
            "status": status,
            "path": path,
            "confidence": confidence,
            "title": title,
            "keywords": keywords,
            "rationale": rationale,
            "raw_response": text,
        }
