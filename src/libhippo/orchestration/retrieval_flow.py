"""Adaptive Retrieval Loop.

Coordinates tiered retrieval and triggers the Maker-Checker governance pipeline
when critical knowledge misses are detected.
"""

from __future__ import annotations

import logging

from libhippo.orchestration.maker_checker import MakerCheckerOrchestrator
from libhippo.tools.retrieval import (
    CriticalityTier,
    EffortTier,
    KnowledgeDispatcher,
    KnowledgeRetrievalResult,
)

logger = logging.getLogger(__name__)


class AdaptiveRetrievalWorkflow:
    """Coordinates tiered knowledge retrieval with automated Maker-Checker escalation on mandatory misses."""

    def __init__(
        self,
        dispatcher: KnowledgeDispatcher,
        orchestrator: MakerCheckerOrchestrator | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.orchestrator = orchestrator

    async def query(
        self,
        query: str,
        effort: EffortTier = "medium",
        criticality: CriticalityTier = "preferred",
    ) -> KnowledgeRetrievalResult:
        """Execute tiered retrieval, escalating to curation and governance on mandatory misses."""
        res = await self.dispatcher.query_knowledge(
            query=query,
            effort=effort,
            criticality=criticality,
        )

        # If mandatory miss and orchestrator available, trigger curation & governance loop
        if res.status == "MISS:MANDATORY" and self.orchestrator and self.orchestrator.curator:
            logger.info(f"Triggering Maker-Checker curation for mandatory query: {query}")
            try:
                gov_res = await self.orchestrator.curate_and_govern(topic=query)
                if gov_res.status == "COMMITTED":
                    # Read back committed node from store
                    node = await self.dispatcher.store.get_node(gov_res.path)
                    summary = (
                        await self.dispatcher.store.read_section(gov_res.path, section="summary")
                        if node
                        else None
                    )
                    snippet = summary or (node.body[:500] if node else gov_res.message)
                    title = node.frontmatter.title if node and node.frontmatter else gov_res.path

                    return KnowledgeRetrievalResult(
                        status="HIT",
                        path=gov_res.path,
                        title=title,
                        snippet=snippet,
                        confidence=0.90,
                        effort_tier=effort,
                        criticality=criticality,
                        source="curator",
                        curated_draft=node.markdown if node else "",
                    )
                else:
                    return KnowledgeRetrievalResult(
                        status="MISS:MANDATORY",
                        path=gov_res.path,
                        snippet=f"[MISS:MANDATORY: Curation failed ({gov_res.status}) - {gov_res.message}]",
                        effort_tier=effort,
                        criticality=criticality,
                        source="curator",
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Adaptive retrieval curation failed: {e}")

        return res
