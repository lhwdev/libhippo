"""BookKeeperAgent: Fast, deterministic librarian & retrieval inspector powered by TypeSafe Jev."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field
from typesafe_sdk import Choice, Noul, Score

from libhippo.agents.base import BaseHippoAgent
from libhippo.models.llm import (
    create_typesafe_client,
    default_model_registry,
    get_model_config,
)
from libhippo.storage.store import KnowledgeStore

logger = logging.getLogger(__name__)


class TypeSafeClientProtocol(Protocol):
    """Protocol for TypeSafe clients to enable easy mocking and stubbing."""

    async def system_one(
        self,
        *,
        state: dict[str, Any],
        questions: dict[str, Any],
        model: str | None = None,
    ) -> Any: ...


def extract_structural_digest(markdown: str, max_chars: int = 1200) -> str:
    """Extract an adaptive structural outline digest within a length budget."""
    if not markdown:
        return ""

    # H6: Strip noise (frontmatter, code blocks, images, link targets)
    body = re.sub(r"^\s*---.*?---\s*", "", markdown, flags=re.DOTALL).strip()
    body = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    body = re.sub(r"!\[.*?\]\(.*?\)", "", body)
    body = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", body)
    cleaned = re.sub(r"\n{3,}", "\n\n", body).strip()

    # Small document fast-path
    if len(cleaned) <= max_chars:
        return cleaned

    # Parse headers and section content
    lines = cleaned.splitlines()
    sections: list[tuple[int, str, list[str]]] = []
    current_header = ""
    current_level = 0
    current_body_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        m = re.match(r"^(#{1,3})\s+(.*)", stripped)
        if m:
            if current_header or current_body_lines:
                sections.append((current_level, current_header, current_body_lines))
            current_level = len(m.group(1))
            current_header = stripped
            current_body_lines = []
        else:
            if stripped:
                current_body_lines.append(stripped)
    if current_header or current_body_lines:
        sections.append((current_level, current_header, current_body_lines))

    # H3: Extract unique inline code symbols up to 8
    raw_symbols = re.findall(r"`([A-Za-z0-9_$.-]{2,40})`", cleaned)
    seen_sym: set[str] = set()
    symbols: list[str] = []
    for s in raw_symbols:
        if s not in seen_sym:
            seen_sym.add(s)
            symbols.append(s)
            if len(symbols) >= 8:
                break

    # Tier 1 & 2: Header skeleton + first sentence under each section
    tier2_entries: list[tuple[int, str, str]] = []
    for lvl, hdr, blines in sections:
        lead_sentence = ""
        if blines:
            first_para = " ".join(blines[:2])
            sentences = re.split(r"(?<=[.!?])\s+", first_para)
            lead_sentence = sentences[0].strip() if sentences else ""
        tier2_entries.append((lvl, hdr, lead_sentence))

    # Adaptive guard: drop subsection leads if budget is tight
    est_len = sum(len(h) + len(s) + 2 for _, h, s in tier2_entries)
    drop_h3_leads = est_len > 0.75 * max_chars

    out_parts: list[str] = []
    for lvl, hdr, lead in tier2_entries:
        if hdr:
            out_parts.append(hdr)
        if lead and (lvl < 3 or not drop_h3_leads):
            out_parts.append(lead)

    digest = "\n".join(out_parts)

    # Tier 3: Append symbol inventory if within remaining budget
    if symbols:
        sym_text = f"\nSymbols: {', '.join(symbols)}"
        if len(digest) + len(sym_text) <= 0.90 * max_chars:
            digest += sym_text

    return digest


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
    remove_tags: list[str] = Field(
        default_factory=list,
        description="Obsolete tags to remove",
    )
    rationale: str = Field(default="", description="Concise explanation for hit or miss")


class BookKeeperAgent(BaseHippoAgent):
    """Adaptive retrieval inspector powered by TypeSafe Jev System One.

    Evaluates candidate coarseness, topic alignment, and leaf specialization
    with zero token streaming overhead.
    """

    def __init__(
        self,
        name: str = "BookKeeperAgent",
        description: str = "Evaluates retrieval candidate coarseness and inspects leaf specialization.",
        client: TypeSafeClientProtocol | None = None,
        model: str | None = None,
        store: KnowledgeStore | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, description=description)
        cfg = get_model_config("book_keeper")
        self.client = client
        self.model = model or cfg.resolve_model_name()
        self.store = store

    def _build_questions(self) -> dict[str, Any]:
        """Construct TypeSafe Jev questions for retrieval inspection."""
        return {
            "coarseness_fit": Choice(
                instructions="Compare the specificity of the search query against the scope of the document outline.",
                criteria={
                    "optimal": "The document is at the direct and appropriate specificity level for the query (optimal broad overview for broad query, or optimal leaf for specific query).",
                    "too_broad_parent": "The document is a high-level overview while the query requests specific implementation parameters, detailed usage, or a dedicated subtopic.",
                    "too_narrow_child": "The document is a narrow sub-component while the query asks for broad architectural concepts.",
                    "tangential_mention": "The query topic is only mentioned in passing.",
                    "unrelated": "The document does not address the query topic.",
                },
            ),
            "coverage_depth": Score(
                instructions="Assess whether the document provides sufficient depth to satisfy the search query.",
                criteria=[
                    "Passing mention or table of contents only.",
                    "Partial overview: explains general concepts but lacks complete parameters, syntax, or edge cases.",
                    "Exhaustive reference: directly provides required parameters, syntax, and concrete examples.",
                ],
            ),
            "should_specialize_leaf": Noul(
                instructions="Does this query warrant a dedicated sub-document rather than resolving to this candidate?",
            ),
            "should_record_alias": Noul(
                instructions="Is this query an accurate search synonym for this document that should accelerate future lookups?",
            ),
        }

    async def lookup(
        self,
        query: str,
        candidates: list[Any],
        criticality: str = "preferred",
    ) -> dict[str, Any]:
        """Perform a fast, deterministic Jev inspection of candidates."""
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

        top = candidates[0]
        top_path = getattr(top, "path", str(top))
        top_title = getattr(top, "title", "")
        top_conf = float(getattr(top, "confidence", 0.70))

        # Retrieve candidate content for outline digest
        content = ""
        if hasattr(top, "content") and top.content:
            content = top.content
        elif self.store:
            try:
                content = await self.store.read_knowledge(top_path) or ""
            except Exception:
                pass

        outline = extract_structural_digest(content)

        state = {
            "query": query,
            "candidate": {
                "path": top_path,
                "title": top_title,
                "outline": outline,
                "tags": getattr(top, "tags", []),
            },
            "criticality": criticality,
        }

        questions = self._build_questions()

        client = self.client or default_model_registry.get_mock_client("book_keeper")
        if client:
            response = await client.system_one(state=state, questions=questions, model=self.model)
        else:
            async with create_typesafe_client("book_keeper", model=self.model) as typesafe_client:
                response = await typesafe_client.system_one(state=state, questions=questions, model=self.model)

        # Extract typed answers
        if hasattr(response, "choices"):
            coarseness = getattr(response.choices.get("coarseness_fit"), "choice", "optimal")
            coverage = float(getattr(response.scores.get("coverage_depth"), "score", 1.0))
            should_specialize = float(getattr(response.nouls.get("should_specialize_leaf"), "noul", 0.0))
            should_record_alias = float(getattr(response.nouls.get("should_record_alias"), "noul", 0.0))
        elif isinstance(response, dict):
            coarseness = response.get("coarseness_fit", "optimal")
            coverage = float(response.get("coverage_depth", 1.0))
            should_specialize = float(response.get("should_specialize_leaf", 0.0))
            should_record_alias = float(response.get("should_record_alias", 0.0))
        else:
            coarseness = "optimal"
            coverage = 1.0
            should_specialize = 0.0
            should_record_alias = 0.0

        # Decision Mapping
        if should_specialize > 0.60 or coarseness in ("too_broad_parent", "tangential_mention", "unrelated"):
            miss_status = "[MISS:MANDATORY]" if criticality == "mandatory" else "[MISS:GAP]"
            return {
                "status": miss_status,
                "path": None,
                "confidence": 0.30,
                "title": "",
                "keywords": [],
                "rationale": f"Coarseness mismatch ({coarseness}, specialize={should_specialize:.2f}); warrants dedicated sub-document.",
                "raw_response": str(response),
            }

        # HIT
        if should_record_alias > 0.70 and self.store:
            try:
                asyncio.create_task(self._record_search_alias_async(top_path, query))
            except Exception:
                pass

        final_conf = max(top_conf, 0.85)
        return {
            "status": "[HIT]",
            "path": top_path,
            "confidence": final_conf,
            "title": top_title,
            "keywords": [],
            "rationale": f"Candidate matches query scope ({coarseness}, depth={coverage:.1f}).",
            "raw_response": str(response),
        }

    async def _record_search_alias_async(self, path: str, query: str) -> None:
        """Asynchronously record decayed search alias in vector store."""
        if not self.store:
            return
        try:
            await self.store.record_search_alias(path, query)
        except Exception as e:
            logger.debug(f"Failed to record search alias for {path}: {e}")

    @staticmethod
    def parse_output(text: str) -> dict[str, Any]:
        """Parse structured output format into dictionary (backward compatibility)."""
        trimmed = text.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                data = json.loads(trimmed)
                return {
                    "status": data.get("status", "[MISS:FALLBACK]"),
                    "path": data.get("path"),
                    "confidence": float(data.get("confidence", 0.5)),
                    "title": data.get("title", ""),
                    "keywords": data.get("keywords", []),
                    "remove_tags": data.get("remove_tags", []),
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

        keywords = []
        kw_match = re.search(r"KEYWORDS:\s*([^\n\r]+)", text)
        if kw_match:
            keywords = [k.strip() for k in kw_match.group(1).split(",") if k.strip()]

        remove_tags = []
        rem_match = re.search(r"REMOVE_TAGS:\s*([^\n\r]+)", text)
        if rem_match:
            remove_tags = [k.strip() for k in rem_match.group(1).split(",") if k.strip()]

        rat_match = re.search(r"RATIONALE:\s*([^\n\r]+)", text)
        rationale = rat_match.group(1).strip() if rat_match else ""

        return {
            "status": status,
            "path": path,
            "confidence": confidence,
            "title": title,
            "keywords": keywords,
            "remove_tags": remove_tags,
            "rationale": rationale,
            "raw_response": text,
        }
