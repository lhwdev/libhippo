"""3-Tier Adaptive Knowledge Retrieval tool (query_knowledge) and dispatcher."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from libhippo.models.knowledge import KnowledgeCandidate
from libhippo.storage.store import KnowledgeStore

logger = logging.getLogger(__name__)

EffortTier = Literal["low", "medium", "high"]
CriticalityTier = Literal["mandatory", "preferred", "optional"]
RetrievalStatus = Literal["HIT", "MISS:FALLBACK", "MISS:MANDATORY", "MISS:OPTIONAL", "HANDOUT:EXPLORE"]
RefineFeedback = Literal["too_broad", "too_narrow", "wrong_direction", "more_details"]


DEFAULT_THRESHOLD_LOW: float = 0.68
DEFAULT_THRESHOLD_MEDIUM: float = 0.80


def apply_rejection(matches: list[Any], feedback: RefineFeedback | None, rejected_path: str) -> list[Any]:
    """Drop rejected candidate and re-rank by Hub-and-Leaf relation to it.

    - too_broad / more_details: drop rejected path and its ancestors; prefer its children.
    - too_narrow: drop rejected path and its children; prefer its ancestors.
    - wrong_direction: drop rejected path and its whole subtree.
    """
    def is_child(p: str) -> bool:
        return p.startswith(f"{rejected_path}/")

    def is_ancestor(p: str) -> bool:
        return rejected_path.startswith(f"{p}/")

    if feedback in ("too_broad", "more_details"):
        dropped, preferred = is_ancestor, is_child
    elif feedback == "too_narrow":
        dropped, preferred = is_child, is_ancestor
    else:
        dropped, preferred = is_child, None

    kept = [m for m in matches if m.path != rejected_path and not dropped(m.path)]
    if preferred:
        kept.sort(key=lambda m: not preferred(m.path))
    return kept


class KnowledgeRetrievalResult(BaseModel):
    """Result of query_knowledge dispatched across effort tiers and criticality routing."""

    status: RetrievalStatus = "HIT"
    path: str | None = None
    title: str = ""
    content: str = ""
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
        orchestrator: Any | None = None,
        threshold_low: float = DEFAULT_THRESHOLD_LOW,
        threshold_medium: float = DEFAULT_THRESHOLD_MEDIUM,
        on_event: Any | None = None,
    ) -> None:
        self.store = store
        self.orchestrator = orchestrator
        self.curator = curator or (orchestrator.curator if orchestrator else None)
        self.checker = checker or (orchestrator.checker if orchestrator else None)
        self.book_keeper = book_keeper
        self.threshold_low = threshold_low
        self.threshold_medium = threshold_medium
        self.on_event = on_event

    async def query_knowledge(
        self,
        query: str,
        effort: EffortTier = "medium",
        criticality: CriticalityTier = "preferred",
        feedback: RefineFeedback | None = None,
        rejected_path: str | None = None,
    ) -> KnowledgeRetrievalResult:
        """Execute 3-tier adaptive retrieval with confidence scoring, metadata visibility, and staleness routing.

        `feedback` and `rejected_path` refine a prior result: the rejected candidate is excluded and
        remaining candidates are re-ranked by hierarchy relation (see `apply_rejection`).
        """
        rejection = {"path": rejected_path, "feedback": feedback} if feedback or rejected_path else None

        async def search(top_k: int) -> list[Any]:
            if not rejected_path:
                return await self.store.search(query=query, top_k=top_k)
            matches = await self.store.search(query=query, top_k=top_k + 5)
            return apply_rejection(matches, feedback, rejected_path)[:top_k]

        # 1. Low Effort Tier: Fast local vector/FTS search, strict cutoff
        if effort == "low":
            matches = await search(top_k=1)
            if matches and matches[0].confidence >= self.threshold_low:
                top = matches[0]
                full_content = await self.store.read_knowledge(top.path) or top.content
                res = KnowledgeRetrievalResult(
                    status="HIT",
                    path=top.path,
                    title=top.title,
                    content=full_content,
                    confidence=top.confidence,
                    effort_tier="low",
                    criticality=criticality,
                    source="fast_path",
                )
                return await self._apply_staleness_routing(res, effort="low", criticality=criticality)
            # Low effort fails fast without LLM invocation
            return self._handle_miss(query, effort="low", criticality=criticality)

        # 2. Medium Effort Tier: Optimistic fast path
        if effort == "medium":
            matches = await search(top_k=3)
            if matches and matches[0].confidence >= self.threshold_medium:
                top = matches[0]
                full_content = await self.store.read_knowledge(top.path) or top.content
                res = KnowledgeRetrievalResult(
                    status="HIT",
                    path=top.path,
                    title=top.title,
                    content=full_content,
                    confidence=top.confidence,
                    effort_tier="medium",
                    criticality=criticality,
                    source="fast_path",
                )
                return await self._apply_staleness_routing(res, effort="medium", criticality=criticality)

            # Below medium threshold -> escalate to BookKeeperAgent if available and matches exist
            if self.book_keeper and matches:
                bk_res = await self._invoke_bookkeeper(query, candidates=matches, criticality=criticality, rejection=rejection)
                if bk_res.status == "HIT":
                    return await self._apply_staleness_routing(bk_res, effort="medium", criticality=criticality)
                return await self._handle_miss_async(query, effort="medium", criticality=criticality)

            # BookKeeper missed or not provided
            return await self._handle_miss_async(query, effort="medium", criticality=criticality)

        # 3. High Effort Tier: Deep BookKeeper exploration & TaskSolver exploration handout
        if effort == "high":
            matches = await search(top_k=5)
            if self.book_keeper and matches:
                bk_res = await self._invoke_bookkeeper(query, candidates=matches, criticality=criticality, rejection=rejection)
                if bk_res.status == "HIT":
                    return await self._apply_staleness_routing(bk_res, effort="high", criticality=criticality)

            # Fallback to top vector match only if high confidence
            if matches and matches[0].confidence >= self.threshold_medium:
                top = matches[0]
                full_content = await self.store.read_knowledge(top.path) or top.content
                res = KnowledgeRetrievalResult(
                    status="HIT",
                    path=top.path,
                    title=top.title,
                    content=full_content,
                    confidence=top.confidence,
                    effort_tier="high",
                    criticality=criticality,
                    source="fast_path",
                )
                return await self._apply_staleness_routing(res, effort="high", criticality=criticality)

            # High-effort miss: Hand out to TaskSolver for context-driven exploration
            if criticality != "mandatory":
                rejected_note = f" Previously rejected `{rejected_path}` ({feedback or 'rejected'}); do not settle on it." if rejected_path else ""
                return KnowledgeRetrievalResult(
                    status="HANDOUT:EXPLORE",
                    effort_tier="high",
                    criticality=criticality,
                    source="none",
                    content=(
                        "[HANDOUT:EXPLORE: No exact match found for query. "
                        "Use search_knowledge / read_knowledge to investigate knowledges, then you MUST complete retrieval with `complete_retrieval` tool. "
                        f"you SHOULD ONLY run tasks related to searching knowledge related to \"query\".{rejected_note}]"
                    ),
                    details={"query": query, "handout_reason": "high_effort_miss", "rejection": rejection},
                )

            return await self._handle_miss_async(query, effort="high", criticality=criticality)

        return self._handle_miss(query, effort=effort, criticality=criticality)

    async def _apply_staleness_routing(
        self,
        result: KnowledgeRetrievalResult,
        effort: EffortTier,
        criticality: CriticalityTier,
    ) -> KnowledgeRetrievalResult:
        """Route retrieved knowledge based on staleness and criticality/effort parameters."""
        if result.status != "HIT" or not result.path:
            return result

        entry = await self.store.catalog.get(result.path)
        if not entry:
            return result

        freshness_status = entry.get("freshness_status", "fresh")
        upstream = entry.get("upstream_version")
        current = entry.get("version", "1.0.0")

        # Metadata banner for web-fetched knowledge
        meta_banner = ""
        is_web_fetched = (
            entry.get("namespace") == "common"
            or bool(entry.get("source"))
            or bool(entry.get("version_check"))
        )
        if is_web_fetched:
            last_up = (entry.get("last_updated") or "unknown")[:10]
            last_chk = (entry.get("last_checked_at") or "never")[:10]
            srcs = entry.get("source") or []
            srcs_str = ", ".join(srcs) if srcs else "none"
            meta_banner = (
                f"[Knowledge Metadata | version: {current} | last_updated: {last_up} | "
                f"last_checked: {last_chk} | status: {freshness_status} | source: {srcs_str}]\n\n"
            )

        if freshness_status != "stale":
            result.content = f"{meta_banner}{result.content}"
            return result

        # Document is STALE. Apply 3-tier routing:
        # Mode C: Wait for latest (mandatory + high)
        if criticality == "mandatory" and effort == "high":
            try:
                reval_res = await self.store.modify_knowledge("revalidate", result.path)
                if reval_res.get("revalidation") == "updated":
                    updated_node = await self.store.get_node(result.path)
                    new_content = updated_node.markdown if updated_node else result.content
                    result.content = f"[NOTICE: Node was refreshed from upstream v{upstream}]\n\n{new_content}"
                    result.details["staleness_action"] = "waited_and_updated"
                    return result
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Wait-for-latest revalidation failed for {result.path}: {e}")

        # Mode A: No fetch (optional OR low effort)
        if criticality == "optional" or effort == "low":
            advisory = (
                f"[NOTICE: This knowledge is outdated (upstream v{upstream or 'newer'} vs doc v{current}). "
                f"Query with higher criticality/effort to update automatically.]\n\n"
            )
            result.content = f"{meta_banner}{advisory}{result.content}"
            result.details["staleness_action"] = "no_fetch_advisory"
            return result

        # Mode B: Stale-While-Revalidate (preferred default, or mandatory with low/med effort)
        notice = (
            f"[NOTICE: This knowledge is outdated (upstream v{upstream or 'newer'} vs doc v{current}). "
            f"Background revalidation queued; using current version for now.]\n\n"
        )
        result.content = f"{meta_banner}{notice}{result.content}"
        result.details["staleness_action"] = "stale_while_revalidate"

        # Queue asynchronous background revalidation
        try:
            asyncio.create_task(self.store.modify_knowledge("revalidate", result.path))
        except Exception:  # noqa: BLE001
            pass

        return result

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
                content=f"[MISS:OPTIONAL: No high-confidence knowledge found for query '{query}']",
            )
        elif criticality == "mandatory":
            return KnowledgeRetrievalResult(
                status="MISS:MANDATORY",
                effort_tier=effort,
                criticality=criticality,
                source="none",
                content=f"[MISS:MANDATORY: Critical knowledge missing for query '{query}']",
            )
        else:
            return KnowledgeRetrievalResult(
                status="MISS:FALLBACK",
                effort_tier=effort,
                criticality=criticality,
                source="none",
                content=f"[MISS:FALLBACK: Proceed with internal model knowledge for query '{query}']",
            )

    async def _handle_miss_async(
        self,
        query: str,
        effort: EffortTier,
        criticality: CriticalityTier,
    ) -> KnowledgeRetrievalResult:
        """Handle miss with potential CuratorAgent scraping for MANDATORY criticality."""
        if criticality == "mandatory":
            if self.orchestrator and getattr(self.orchestrator, "curator", None):
                try:
                    gov_res = await self.orchestrator.curate_and_govern(
                        topic=query,
                        on_event=self.on_event,
                    )
                    if gov_res.status == "COMMITTED":
                        node = await self.store.get_node(gov_res.path)
                        full_content = (
                            await self.store.read_knowledge(gov_res.path)
                            if node
                            else None
                        )
                        content = full_content or (node.markdown if node else gov_res.message)
                        title = node.frontmatter.title if node and node.frontmatter else gov_res.path
                        return KnowledgeRetrievalResult(
                            status="HIT",
                            path=gov_res.path,
                            title=title,
                            content=content,
                            confidence=0.92,
                            effort_tier=effort,
                            criticality=criticality,
                            source="curator",
                            curated_draft=node.markdown if node else "",
                            details={"orchestration": "maker_checker_governed"},
                        )

                    return KnowledgeRetrievalResult(
                        status="MISS:MANDATORY",
                        path=gov_res.path,
                        effort_tier=effort,
                        criticality=criticality,
                        source="curator",
                        curated_draft=gov_res.message,
                        content=f"[MISS:MANDATORY for query '{query}': {gov_res.message}]",
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Orchestrated curation failed: {e}")
            elif self.curator:
                try:
                    # Trigger CuratorAgent web scrape and draft proposal
                    curation = await self.curator.curate(query)
                    draft_md = curation.get("draft", "") if isinstance(curation, dict) else str(curation)
                    target_path = curation.get("path", f"common/web/{query.replace(' ', '_').lower()}.md") if isinstance(curation, dict) else f"common/web/{query.replace(' ', '_').lower()}.md"

                    # If checker is available, audit the draft
                    if self.checker and draft_md:
                        candidate = KnowledgeCandidate.from_markdown(target_path, draft_md)
                        report = await self.checker.check(candidate)
                        if report.verdict == "PASS":
                            # Auto-commit routine PASS
                            await self.store.modify_knowledge("create", target_path, draft_md)
                            return KnowledgeRetrievalResult(
                                status="HIT",
                                path=target_path,
                                title=candidate.frontmatter.title if candidate.frontmatter else target_path,
                                content=candidate.markdown,
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
                        content=draft_md if draft_md else f"[MISS:MANDATORY for query '{query}']",
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"CuratorAgent invocation failed: {e}")

        return self._handle_miss(query, effort=effort, criticality=criticality)

    async def _invoke_bookkeeper(
        self,
        query: str,
        candidates: list[Any],
        criticality: CriticalityTier,
        rejection: dict[str, Any] | None = None,
    ) -> KnowledgeRetrievalResult:
        """Invoke BookKeeperAgent for query expansion and cross-referencing."""
        if not self.book_keeper or not candidates:
            return self._handle_miss(query, effort="medium", criticality=criticality)
        try:
            extra: dict[str, Any] = {"rejection": rejection} if rejection else {}
            bk_response = await self.book_keeper.lookup(query=query, candidates=candidates, criticality=criticality, **extra)
            if isinstance(bk_response, dict):
                status_str = bk_response.get("status", "HIT")
                status_tag: RetrievalStatus = (
                    "HIT" if status_str == "HIT" or "[HIT]" in status_str else (
                        "MISS:MANDATORY" if "MANDATORY" in status_str else "MISS:FALLBACK"
                    )
                )
                hit_path = bk_response.get("path")
                full_content = ""
                if hit_path and status_tag == "HIT":
                    keywords = list(bk_response.get("keywords") or [])
                    remove_tags = list(bk_response.get("remove_tags") or [])
                    if keywords or remove_tags:
                        try:
                            await self.store.improve_search_confidence(
                                hit_path,
                                keywords=keywords,
                                remove_tags=remove_tags,
                            )
                        except Exception as e:  # noqa: BLE001
                            logger.warning(f"Failed to improve search confidence for {hit_path}: {e}")
                    full_content = await self.store.read_knowledge(hit_path) or ""

                return KnowledgeRetrievalResult(
                    status=status_tag,
                    path=hit_path,
                    title=bk_response.get("title", ""),
                    content=full_content,
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
