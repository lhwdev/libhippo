"""3-Tier Adaptive Knowledge Retrieval tool (query_knowledge) and dispatcher."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from libhippo.storage.store import KnowledgeStore

logger = logging.getLogger(__name__)

EffortTier = Literal["low", "medium", "high"]
CriticalityTier = Literal["mandatory", "preferred", "optional"]
RetrievalStatus = Literal["HIT", "MISS:FALLBACK", "MISS:MANDATORY", "MISS:OPTIONAL"]


class KnowledgeRetrievalResult(BaseModel):
    """Result of query_knowledge dispatched across effort tiers and criticality routing."""

    status: RetrievalStatus = "HIT"
    path: str | None = None
    title: str = ""
    snippet: str = ""
    confidence: float = 0.0
    effort_tier: EffortTier = "medium"
    criticality: CriticalityTier = "preferred"
    source: Literal["fast_path", "book_keeper", "curator", "none"] = "none"
    curated_draft: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class KnowledgeDispatcher:
    """Dispatches knowledge queries across 3 effort tiers and coordinates agent fallbacks."""

    def __init__(
        self,
        store: KnowledgeStore,
        book_keeper: Any | None = None,
        curator: Any | None = None,
        checker: Any | None = None,
        threshold_low: float = 0.70,
        threshold_medium: float = 0.82,
    ) -> None:
        self.store = store
        self.book_keeper = book_keeper
        self.curator = curator
        self.checker = checker
        self.threshold_low = threshold_low
        self.threshold_medium = threshold_medium

    async def query_knowledge(
        self,
        query: str,
        effort: EffortTier = "medium",
        criticality: CriticalityTier = "preferred",
    ) -> KnowledgeRetrievalResult:
        """Execute 3-tier adaptive retrieval with confidence scoring and criticality routing."""
        # 1. Low Effort Tier: Fast local vector/FTS search, strict cutoff
        if effort == "low":
            matches = await self.store.search(query=query, top_k=1)
            if matches and matches[0].confidence >= self.threshold_low:
                top = matches[0]
                return KnowledgeRetrievalResult(
                    status="HIT",
                    path=top.path,
                    title=top.title,
                    snippet=top.snippet,
                    confidence=top.confidence,
                    effort_tier="low",
                    criticality=criticality,
                    source="fast_path",
                )
            # Low effort fails fast without LLM invocation
            return self._handle_miss(query, effort="low", criticality=criticality)

        # 2. Medium Effort Tier: Optimistic fast path
        if effort == "medium":
            matches = await self.store.search(query=query, top_k=3)
            if matches and matches[0].confidence >= self.threshold_medium:
                top = matches[0]
                return KnowledgeRetrievalResult(
                    status="HIT",
                    path=top.path,
                    title=top.title,
                    snippet=top.snippet,
                    confidence=top.confidence,
                    effort_tier="medium",
                    criticality=criticality,
                    source="fast_path",
                )
            # Below medium threshold -> escalate to BookKeeperAgent if available
            if self.book_keeper:
                bk_res = await self._invoke_bookkeeper(query, candidates=matches, criticality=criticality)
                if bk_res.status == "HIT":
                    return bk_res

            # BookKeeper missed or not provided
            return await self._handle_miss_async(query, effort="medium", criticality=criticality)

        # 3. High Effort Tier: Vector seed match + deep BookKeeper exploration
        if effort == "high":
            matches = await self.store.search(query=query, top_k=5)
            if self.book_keeper:
                bk_res = await self._invoke_bookkeeper(query, candidates=matches, criticality=criticality)
                if bk_res.status == "HIT":
                    return bk_res

            # Direct fallback to top vector match if good confidence
            if matches and matches[0].confidence >= self.threshold_low:
                top = matches[0]
                return KnowledgeRetrievalResult(
                    status="HIT",
                    path=top.path,
                    title=top.title,
                    snippet=top.snippet,
                    confidence=top.confidence,
                    effort_tier="high",
                    criticality=criticality,
                    source="fast_path",
                )

            return await self._handle_miss_async(query, effort="high", criticality=criticality)

        return self._handle_miss(query, effort=effort, criticality=criticality)

    def _handle_miss(
        self,
        query: str,
        effort: EffortTier,
        criticality: CriticalityTier,
    ) -> KnowledgeRetrievalResult:
        """Deterministic miss handling when no asynchronous curator is needed or available."""
        if criticality == "optional":
            return KnowledgeRetrievalResult(
                status="MISS:OPTIONAL",
                effort_tier=effort,
                criticality=criticality,
                source="none",
                snippet=f"[MISS:OPTIONAL: No high-confidence knowledge found for query '{query}']",
            )
        elif criticality == "mandatory":
            return KnowledgeRetrievalResult(
                status="MISS:MANDATORY",
                effort_tier=effort,
                criticality=criticality,
                source="none",
                snippet=f"[MISS:MANDATORY: Critical knowledge missing for query '{query}']",
            )
        else:
            return KnowledgeRetrievalResult(
                status="MISS:FALLBACK",
                effort_tier=effort,
                criticality=criticality,
                source="none",
                snippet=f"[MISS:FALLBACK: Proceed with internal model knowledge for query '{query}']",
            )

    async def _handle_miss_async(
        self,
        query: str,
        effort: EffortTier,
        criticality: CriticalityTier,
    ) -> KnowledgeRetrievalResult:
        """Handle miss with potential CuratorAgent scraping for MANDATORY criticality."""
        if criticality == "mandatory" and self.curator:
            try:
                # Trigger CuratorAgent web scrape and draft proposal
                curation = await self.curator.curate(query)
                draft_md = curation.get("draft", "") if isinstance(curation, dict) else str(curation)
                target_path = curation.get("path", f"common/web/{query.replace(' ', '_').lower()}.md") if isinstance(curation, dict) else f"common/web/{query.replace(' ', '_').lower()}.md"

                # If checker is available, audit the draft
                if self.checker and draft_md:
                    from libhippo.models.knowledge import KnowledgeCandidate
                    candidate = KnowledgeCandidate.from_markdown(target_path, draft_md)
                    report = await self.checker.check(candidate)
                    if report.verdict == "PASS":
                        # Auto-commit routine PASS
                        await self.store.modify_knowledge("create", target_path, draft_md)
                        return KnowledgeRetrievalResult(
                            status="HIT",
                            path=target_path,
                            title=candidate.frontmatter.title if candidate.frontmatter else target_path,
                            snippet=candidate.body[:500],
                            confidence=0.90,
                            effort_tier=effort,
                            criticality=criticality,
                            source="curator",
                            curated_draft=draft_md,
                        )

                return KnowledgeRetrievalResult(
                    status="MISS:MANDATORY",
                    path=target_path,
                    effort_tier=effort,
                    criticality=criticality,
                    source="curator",
                    curated_draft=draft_md,
                    snippet=draft_md[:500] if draft_md else f"[MISS:MANDATORY for query '{query}']",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"CuratorAgent invocation failed: {e}")

        return self._handle_miss(query, effort=effort, criticality=criticality)

    async def _invoke_bookkeeper(
        self,
        query: str,
        candidates: list[Any],
        criticality: CriticalityTier,
    ) -> KnowledgeRetrievalResult:
        """Invoke BookKeeperAgent for query expansion and cross-referencing."""
        try:
            bk_response = await self.book_keeper.lookup(query=query, candidates=candidates, criticality=criticality)
            if isinstance(bk_response, dict):
                status_str = bk_response.get("status", "HIT")
                status_tag: RetrievalStatus = (
                    "HIT" if status_str == "HIT" or "[HIT]" in status_str else (
                        "MISS:MANDATORY" if "MANDATORY" in status_str else "MISS:FALLBACK"
                    )
                )
                return KnowledgeRetrievalResult(
                    status=status_tag,
                    path=bk_response.get("path"),
                    title=bk_response.get("title", ""),
                    snippet=bk_response.get("snippet", ""),
                    confidence=float(bk_response.get("confidence", 0.85)),
                    effort_tier="medium",
                    criticality=criticality,
                    source="book_keeper",
                    details=bk_response,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"BookKeeperAgent failed: {e}")

        return KnowledgeRetrievalResult(
            status="MISS:FALLBACK",
            effort_tier="medium",
            criticality=criticality,
            source="none",
        )
