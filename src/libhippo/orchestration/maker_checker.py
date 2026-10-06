"""Maker-Checker Governance Workflow.

Coordinates CuratorAgent (Maker/Draftsman), CheckerAgent (Deterministic Gatekeeper),
and VerifierAgent (Refactoring Authority) with atomic disk mutation controls.
"""

from __future__ import annotations

import asyncio
import datetime
import io
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from rich.console import Console
from rich.text import Text

from libhippo.agents.checker import CheckerAgent
from libhippo.agents.curator import CuratorAgent
from libhippo.agents.verifier import VerifierAgent
from libhippo.models.audit import JevAuditReport
from libhippo.models.knowledge import (
    KnowledgeAgentEvent,
    KnowledgeCandidate,
    KnowledgeContext,
    reconcile_candidate_frontmatter,
)
from libhippo.storage.mount import ReadOnlyMountError
from libhippo.storage.store import KnowledgeStore

logger = logging.getLogger(__name__)

GovernanceStatus = Literal[
    "COMMITTED",
    "ESCALATED",
    "MERGED",
    "REJECTED",
    "REVISE_FAILED",
]


@dataclass
class GovernanceTurn:
    """A single turn in the iterative Maker-Checker review history."""

    round_index: int
    actor: str
    action: str
    summary: str
    token_count: int = 0
    candidate_path: str = ""


class RefactoringContextManager:
    """Manages linear turn history stacking and head-compact-tail compression for refactoring."""

    def __init__(self, token_threshold: int = 8000, keep_tail_turns: int = 2) -> None:
        self.token_threshold = token_threshold
        self.keep_tail_turns = keep_tail_turns
        self.turns: list[GovernanceTurn] = []

    def add_turn(self, turn: GovernanceTurn) -> None:
        """Append a new review turn and perform head-compact-tail compaction if over budget."""
        self.turns.append(turn)
        self.maybe_compact()

    def total_tokens(self) -> int:
        """Estimate total tokens across accumulated turns."""
        return sum(t.token_count for t in self.turns)

    def maybe_compact(self) -> bool:
        """Compact intermediate turns if total token count exceeds threshold.

        Preserves:
        - Head (Turn 0: initial candidate / goal)
        - Compacted Intermediate (concise revision pointers)
        - Tail (Last N active turns)
        """
        if self.total_tokens() <= self.token_threshold or len(self.turns) <= self.keep_tail_turns + 2:
            return False

        head = self.turns[0]
        tail = self.turns[-self.keep_tail_turns:]
        intermediate = self.turns[1 : -self.keep_tail_turns]

        compact_summary = "; ".join(
            f"[R{t.round_index} ({t.actor}): {t.action} -> {t.summary[:120]}]"
            for t in intermediate
        )
        compacted_turn = GovernanceTurn(
            round_index=intermediate[0].round_index,
            actor="Orchestrator:Compactor",
            action="compacted_intermediate_history",
            summary=compact_summary,
            token_count=max(20, len(compact_summary.split())),
        )

        self.turns = [head, compacted_turn, *tail]
        return True


@dataclass
class MakerCheckerResult:
    """Outcome of a Maker-Checker governance evaluation and commit cycle."""

    status: GovernanceStatus
    path: str
    verdict: str
    report: JevAuditReport | None = None
    committed_paths: list[str] = field(default_factory=list)
    retries_used: int = 0
    history: list[GovernanceTurn] = field(default_factory=list)
    message: str = ""


class MakerCheckerOrchestrator:
    """Coordinates Maker-Checker governance lifecycle with atomic disk mutation controls."""

    def __init__(
        self,
        store: KnowledgeStore,
        checker: CheckerAgent | None = None,
        curator: CuratorAgent | None = None,
        verifier: VerifierAgent | None = None,
        context_manager: RefactoringContextManager | None = None,
        on_event: Callable[[KnowledgeAgentEvent], Any] | None = None,
    ) -> None:
        self.store = store
        self.checker = checker or CheckerAgent()
        self.curator = curator
        self.verifier = verifier
        if self.curator:
            self.curator.orchestrator = self
            if not getattr(self.curator, "store", None):
                self.curator.store = store
        self.context_manager = context_manager or RefactoringContextManager()
        self.on_event = on_event

    async def _emit(self, event: KnowledgeAgentEvent, on_event: Callable[[KnowledgeAgentEvent], Any] | None = None) -> None:
        """Emit real-time observability event to stream and UI."""
        if not event.timestamp:
            event.timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        summary = event.output_summary or event.input_summary or ""

        status_style = "bold not dim green" if event.status in ("PASS", "READY", "COMMITTED", "MERGED") else (
            "bold not dim red" if event.status in ("FAIL", "REJECTED", "ERROR") else "bold not dim cyan"
        )
        evt_t = Text("[", style="dim")
        evt_t.append(event.timestamp, style="dim cyan")
        evt_t.append("] ", style="dim")
        evt_t.append(event.agent, style="bold not dim magenta")
        evt_t.append(f" {event.action} ", style="yellow")
        evt_t.append(f"({event.status})", style=status_style)
        if summary:
            evt_t.append(": ", style="dim")
            evt_t.append(summary, style="dim")

        buf = io.StringIO()
        c = Console(file=buf, force_terminal=True, color_system="standard", width=1000, soft_wrap=True)
        c.print(evt_t)
        logger.info(buf.getvalue().rstrip("\n"))
        cb = on_event or self.on_event
        if cb:
            try:
                res = cb(event)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.debug("KnowledgeAgentEvent emission error: %s", e)

    async def run_governance(
        self,
        candidate: KnowledgeCandidate | str,
        target_path: str | None = None,
        context: KnowledgeContext | None = None,
        max_retries: int = 2,
        on_event: Callable[[KnowledgeAgentEvent], Any] | None = None,
    ) -> MakerCheckerResult:
        """Execute Maker-Checker governance loop with dual-phase audit and safe commits."""
        current_markdown = candidate if isinstance(candidate, str) else candidate.markdown
        current_path = (
            target_path
            or (candidate.path if isinstance(candidate, KnowledgeCandidate) else "common/candidate.md")
        )

        existing_node = None
        try:
            existing_node = await self.store.get_node(current_path)
        except Exception:
            pass

        retries = 0
        round_idx = 0

        while retries <= max_retries:
            round_idx += 1
            node = KnowledgeCandidate.from_markdown(current_path, current_markdown)
            reconcile_candidate_frontmatter(node, previous_candidate=existing_node)
            tokens = self.checker.count_tokens(node.markdown)

            self.context_manager.add_turn(
                GovernanceTurn(
                    round_index=round_idx,
                    actor="Curator/Maker",
                    action="propose_draft",
                    summary=f"Submitted draft for '{node.path}' ({tokens} tokens)",
                    token_count=tokens,
                    candidate_path=node.path,
                )
            )

            # Emit draft proposed event
            await self._emit(
                KnowledgeAgentEvent(
                    agent="CuratorAgent",
                    action="drafting",
                    status="completed",
                    target_path=node.path,
                    output_summary=f"Submitted draft for '{node.path}' ({tokens} tokens)",
                    details={"tokens": tokens, "round": round_idx},
                ),
                on_event,
            )

            # Step 1: Audit via CheckerAgent
            await self._emit(
                KnowledgeAgentEvent(
                    agent="CheckerAgent",
                    action="audit",
                    status="running",
                    target_path=node.path,
                    input_summary=f"Auditing '{node.path}' ({tokens} tokens) via Jev model",
                    details={"tokens": tokens, "round": round_idx},
                ),
                on_event,
            )

            report = await self.checker.check(node, context=context)
            round_idx += 1

            verdict_status = (
                "passed"
                if report.verdict == "PASS"
                else "escalated"
                if report.verdict == "ESCALATE_REFACTOR"
                else "rejected"
            )

            await self._emit(
                KnowledgeAgentEvent(
                    agent="CheckerAgent",
                    action="audit_verdict",
                    status=verdict_status,
                    target_path=node.path,
                    output_summary=f"Verdict: {report.verdict} | Importance: {report.importance_score:.2f} | Size: {report.size_status}",
                    details={
                        "verdict": report.verdict,
                        "size_status": report.size_status,
                        "importance_score": report.importance_score,
                        "coherence_score": report.coherence_score,
                        "critique": "; ".join(report.schema_errors + report.content_errors)[:150],
                    },
                ),
                on_event,
            )

            self.context_manager.add_turn(
                GovernanceTurn(
                    round_index=round_idx,
                    actor="CheckerAgent",
                    action=f"audit_verdict:{report.verdict}",
                    summary=f"Verdict: {report.verdict}, Size: {report.size_status}, Score: {report.importance_score}",
                    token_count=100,
                    candidate_path=node.path,
                )
            )

            # Step 2: Routing based on verdict
            if report.verdict == "PASS":
                # Routine reviews: bypass LLM review and commit directly via modify_knowledge
                try:
                    reconcile_candidate_frontmatter(
                        node,
                        previous_candidate=existing_node,
                        importance=report.importance_score,
                        status="active",
                    )
                    await self.store.modify_knowledge(
                        action="create",
                        path=node.path,
                        content=node.to_markdown() if node.frontmatter else node.markdown,
                    )
                    await self._emit(
                        KnowledgeAgentEvent(
                            agent="MakerChecker",
                            action="commit",
                            status="completed",
                            target_path=node.path,
                            output_summary=f"Committed directly to store at '{node.path}'",
                        ),
                        on_event,
                    )
                    return MakerCheckerResult(
                        status="COMMITTED",
                        path=node.path,
                        verdict="PASS",
                        report=report,
                        committed_paths=[node.path],
                        retries_used=retries,
                        history=list(self.context_manager.turns),
                        message=f"Draft successfully audited and committed to '{node.path}'.",
                    )
                except ReadOnlyMountError as e:
                    return MakerCheckerResult(
                        status="REJECTED",
                        path=node.path,
                        verdict="PASS",
                        report=report,
                        retries_used=retries,
                        history=list(self.context_manager.turns),
                        message=f"Commit rejected due to read-only mount: {e}",
                    )

            elif report.verdict == "MERGE_REQUIRED":
                # Check force_keep: immutable standalone exemption
                if node.frontmatter and node.frontmatter.force_keep:
                    try:
                        reconcile_candidate_frontmatter(
                            node,
                            previous_candidate=existing_node,
                            importance=report.importance_score,
                            status="active",
                        )
                        await self.store.modify_knowledge(
                            action="create",
                            path=node.path,
                            content=node.to_markdown() if node.frontmatter else node.markdown,
                        )
                        await self._emit(
                            KnowledgeAgentEvent(
                                agent="MakerChecker",
                                action="commit",
                                status="completed",
                                target_path=node.path,
                                output_summary=f"Committed standalone node (force_keep=True) at '{node.path}'",
                            ),
                            on_event,
                        )
                        return MakerCheckerResult(
                            status="COMMITTED",
                            path=node.path,
                            verdict="PASS (force_keep)",
                            report=report,
                            committed_paths=[node.path],
                            retries_used=retries,
                            history=list(self.context_manager.turns),
                            message=f"Node '{node.path}' has force_keep=True; preserved as standalone exemption.",
                        )
                    except ReadOnlyMountError as e:
                        return MakerCheckerResult(
                            status="REJECTED",
                            path=node.path,
                            verdict="REJECTED_READ_ONLY",
                            report=report,
                            retries_used=retries,
                            history=list(self.context_manager.turns),
                            message=str(e),
                        )

                # Coalescence handling
                if not report.merge_candidate_siblings and report.merge_recommendation == "none":
                    if report.importance_score >= 0.40:
                        # Retain as standalone exception
                        try:
                            reconcile_candidate_frontmatter(
                                node,
                                previous_candidate=existing_node,
                                importance=report.importance_score,
                                status="active",
                            )
                            await self.store.modify_knowledge(
                                action="create",
                                path=node.path,
                                content=node.to_markdown() if node.frontmatter else node.markdown,
                            )
                            return MakerCheckerResult(
                                status="COMMITTED",
                                path=node.path,
                                verdict="MERGE_EXCEPTION_KEPT",
                                report=report,
                                committed_paths=[node.path],
                                retries_used=retries,
                                history=list(self.context_manager.turns),
                                message=f"Undersized stub retained as standalone exception due to high importance ({report.importance_score}).",
                            )
                        except ReadOnlyMountError as e:
                            return MakerCheckerResult(
                                status="REJECTED",
                                path=node.path,
                                verdict="REJECTED_READ_ONLY",
                                report=report,
                                retries_used=retries,
                                history=list(self.context_manager.turns),
                                message=str(e),
                            )
                    else:
                        # Prune low-importance unmergeable stub
                        return MakerCheckerResult(
                            status="REJECTED",
                            path=node.path,
                            verdict="MERGE_REQUIRED_PRUNED",
                            report=report,
                            retries_used=retries,
                            history=list(self.context_manager.turns),
                            message=f"Undersized stub '{node.path}' pruned due to low importance score ({report.importance_score}).",
                        )

                # Merge with siblings
                target_merge_path = (
                    report.merge_candidate_siblings[0]
                    if report.merge_candidate_siblings
                    else node.path
                )
                try:
                    await self._emit(
                        KnowledgeAgentEvent(
                            agent="MakerChecker",
                            action="merge",
                            status="running",
                            target_path=target_merge_path,
                            input_summary=f"Merging '{node.path}' into sibling '{target_merge_path}'",
                        ),
                        on_event,
                    )
                    if self.verifier:
                        await self.verifier.merge_nodes(
                            target_path=target_merge_path,
                            extra_paths=[node.path],
                            content=node.markdown,
                            store=self.store,
                        )
                    else:
                        await self.store.modify_knowledge(
                            action="merge",
                            path=target_merge_path,
                            extra_paths=[node.path],
                            content=node.markdown,
                        )
                    await self._emit(
                        KnowledgeAgentEvent(
                            agent="MakerChecker",
                            action="merge",
                            status="completed",
                            target_path=target_merge_path,
                            output_summary=f"Successfully merged into sibling '{target_merge_path}'",
                        ),
                        on_event,
                    )
                    return MakerCheckerResult(
                        status="MERGED",
                        path=target_merge_path,
                        verdict="MERGE_REQUIRED",
                        report=report,
                        committed_paths=[target_merge_path],
                        retries_used=retries,
                        history=list(self.context_manager.turns),
                        message=f"Merged into sibling '{target_merge_path}'.",
                    )
                except ReadOnlyMountError as e:
                    return MakerCheckerResult(
                        status="REJECTED",
                        path=target_merge_path,
                        verdict="MERGE_REQUIRED",
                        report=report,
                        retries_used=retries,
                        history=list(self.context_manager.turns),
                        message=str(e),
                    )

            elif report.verdict == "ESCALATE_REFACTOR":
                if not self.verifier:
                    return MakerCheckerResult(
                        status="ESCALATED",
                        path=node.path,
                        verdict="ESCALATE_REFACTOR",
                        report=report,
                        retries_used=retries,
                        history=list(self.context_manager.turns),
                        message="Escalated to VerifierAgent, but no VerifierAgent is registered.",
                    )

                round_idx += 1
                await self._emit(
                    KnowledgeAgentEvent(
                        agent="VerifierAgent",
                        action="refactor",
                        status="running",
                        target_path=node.path,
                        input_summary=f"Resolving refactor escalation for overgrown node '{node.path}'",
                    ),
                    on_event,
                )
                verifier_res = await self.verifier.resolve_escalation(
                    candidate=node,
                    report=report,
                    store=self.store,
                )

                self.context_manager.add_turn(
                    GovernanceTurn(
                        round_index=round_idx,
                        actor="VerifierAgent",
                        action=f"escalation_resolved:{verifier_res.get('status')}",
                        summary=f"Action: {verifier_res.get('action')}, Rationale: {verifier_res.get('rationale')[:100]}",
                        token_count=150,
                        candidate_path=node.path,
                    )
                )

                v_status = "completed" if verifier_res.get("status") == "MUTATION_EXECUTED" else "failed"
                await self._emit(
                    KnowledgeAgentEvent(
                        agent="VerifierAgent",
                        action="refactor",
                        status=v_status,
                        target_path=node.path,
                        output_summary=f"Escalation resolved: {verifier_res.get('status')} ({verifier_res.get('action', 'none')})",
                        details=verifier_res,
                    ),
                    on_event,
                )

                if verifier_res.get("status") == "MUTATION_EXECUTED":
                    child_paths = [c["path"] for c in verifier_res.get("children", [])]
                    return MakerCheckerResult(
                        status="COMMITTED",
                        path=node.path,
                        verdict="ESCALATE_REFACTOR",
                        report=report,
                        committed_paths=[node.path, *child_paths],
                        retries_used=retries,
                        history=list(self.context_manager.turns),
                        message="Oversized node partitioned and committed by VerifierAgent.",
                    )
                elif verifier_res.get("status") == "REVISE_REJECTED":
                    return MakerCheckerResult(
                        status="REJECTED",
                        path=node.path,
                        verdict="ESCALATE_REFACTOR",
                        report=report,
                        retries_used=retries,
                        history=list(self.context_manager.turns),
                        message=verifier_res.get("error", "Refactoring rejected."),
                    )

                return MakerCheckerResult(
                    status="ESCALATED",
                    path=node.path,
                    verdict="ESCALATE_REFACTOR",
                    report=report,
                    retries_used=retries,
                    history=list(self.context_manager.turns),
                    message=f"Refactoring planned by VerifierAgent: {verifier_res.get('rationale')}",
                )

            elif report.verdict in ("REVISE_SCHEMA", "REVISE_CONTENT"):
                if retries < max_retries and self.curator:
                    errors = report.schema_errors + report.content_errors
                    feedback = (
                        f"Audit verdict: {report.verdict}\n"
                        f"Size status: {report.size_status}\n"
                        f"Diagnostics:\n" + "\n".join(f"- {e}" for e in errors)
                    )
                    round_idx += 1
                    await self._emit(
                        KnowledgeAgentEvent(
                            agent="CuratorAgent",
                            action="revise",
                            status="running",
                            target_path=current_path,
                            input_summary=f"Revising draft (round {retries + 1}/{max_retries}) based on Checker critique",
                            details={"diagnostics": errors},
                        ),
                        on_event,
                    )
                    revised = await self.curator.revise(
                        candidate_markdown=current_markdown,
                        feedback=feedback,
                        target_path=current_path,
                    )
                    current_markdown = revised["draft"]
                    retries += 1
                    tokens = self.checker.count_tokens(current_markdown)
                    await self._emit(
                        KnowledgeAgentEvent(
                            agent="CuratorAgent",
                            action="revise",
                            status="completed",
                            target_path=revised.get("path", current_path),
                            output_summary=f"Revision round {retries} produced updated draft ({tokens} tokens)",
                        ),
                        on_event,
                    )
                    continue
                else:
                    return MakerCheckerResult(
                        status="REVISE_FAILED",
                        path=node.path,
                        verdict=report.verdict,
                        report=report,
                        retries_used=retries,
                        history=list(self.context_manager.turns),
                        message=f"Revision retries ({retries}/{max_retries}) exhausted without passing audit.",
                    )

            # Unexpected verdict
            return MakerCheckerResult(
                status="REJECTED",
                path=node.path,
                verdict=report.verdict,
                report=report,
                retries_used=retries,
                history=list(self.context_manager.turns),
                message=f"Unhandled audit verdict: {report.verdict}",
            )

        return MakerCheckerResult(
            status="REVISE_FAILED",
            path=current_path,
            verdict="MAX_RETRIES_EXCEEDED",
            retries_used=retries,
            history=list(self.context_manager.turns),
            message="Maximum revision retries exceeded.",
        )

    async def curate_and_govern(
        self,
        topic: str,
        target_path: str | None = None,
        context: str = "",
        max_retries: int = 2,
        on_event: Callable[[KnowledgeAgentEvent], Any] | None = None,
    ) -> MakerCheckerResult:
        """End-to-end pipeline: research/draft candidate with Curator, then execute governance."""
        if not self.curator:
            raise ValueError("CuratorAgent is required for curate_and_govern.")

        await self._emit(
            KnowledgeAgentEvent(
                agent="CuratorAgent",
                action="drafting",
                status="running",
                target_path=target_path or topic,
                input_summary=f"Researching and drafting knowledge for '{topic}'",
            ),
            on_event,
        )

        curated = await self.curator.curate(
            topic_or_query=topic,
            target_path=target_path,
            context=context,
        )

        if curated.get("gov_result"):
            return curated["gov_result"]

        tokens = self.checker.count_tokens(curated["draft"])
        await self._emit(
            KnowledgeAgentEvent(
                agent="CuratorAgent",
                action="drafting",
                status="completed",
                target_path=curated["path"],
                output_summary=f"Draft produced for '{curated['path']}' ({tokens} tokens)",
                details={"tokens": tokens},
            ),
            on_event,
        )

        return await self.run_governance(
            candidate=curated["draft"],
            target_path=curated["path"],
            max_retries=max_retries,
            on_event=on_event,
        )
