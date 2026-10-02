"""CheckerAgent: Fast, deterministic structural & schema gatekeeper using TypeSafe Jev."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

import tiktoken
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, Score

from libhippo.agents.base import BaseHippoAgent
from libhippo.models.audit import AuditVerdict, JevAuditReport, SizeStatus, TaxonomyFit
from libhippo.models.knowledge import KnowledgeCandidate, KnowledgeContext

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

@dataclass
class TokenCountBoundary:
    lower_trigger: float = 300.0
    lower_target: float = 500.0
    upper_target: float = 1000.0
    upper_trigger: float = 1800.0

class CheckerAgent(BaseHippoAgent):
    """Primary gatekeeper for knowledge reviews in LibHippo.

    Uses the TypeSafe Jev System One model to evaluate candidate markdown nodes
    across structural, sizing, taxonomy, importance, and schema dimensions.
    Bypasses expensive LLM inference for routine reviews that pass all checks.
    """

    def __init__(
        self,
        name: str = "CheckerAgent",
        description: str = "Determines if given knowledge document is good to go.",
        client: TypeSafeClientProtocol | None = None,
        model: str = "jev",
        token_count_bound: TokenCountBoundary | None = None,
    ) -> None:
        super().__init__(name=name, description=description)
        self.client = client
        self.model = model
        self.token_count_bound = token_count_bound or TokenCountBoundary()

        try:
            self._tokenizer = tiktoken.get_encoding("cl100k_base")
        except (ValueError, KeyError):
            self._tokenizer = None

    def count_tokens(self, text: str) -> int:
        """Count raw tokens deterministically using cl100k_base or word heuristic."""
        if self._tokenizer:
            return len(self._tokenizer.encode(text))
        return max(1, len(text.split()) * 4 // 3)

    def _build_questions(self) -> dict[str, Any]:
        """Construct TypeSafe Jev questions."""    

        # Do not use Jev for everything; anything that can checked without models can be simply
        # checked in programmatic way.
        return {
            "taxonomy_fit": Choice(
                instructions=(
                    "Does `candidate.path` logically fit within `parent.path` "
                    "and follow project directory naming conventions?"
                ),
                criteria={
                    "optimal": "Path logically belongs to domain, follows same naming convention, matches content.",
                    "misplaced": "Content belongs under a completely different domain or namespace.",
                    "rename_suggested": "Placement is correct, but filename is ambiguous or contradicts conventions.",
                },
            ),
            "bloatedness": Score(
                instructions=(
                    "Evaluate `candidate.content` for semantic bloat, verbosity, and "
                    "topic stuffing versus concise informational density."
                ),
                criteria=[
                    "Under-developed / Sparse stub: Superficial rules with insufficient depth for a standalone file.",
                    "Lean & Optimal Density: Crisp, high-signal rules with concise edge cases and zero filler.",
                    "Discursive Bloat: Rambling explanations, wordy prose, redundant examples, or minor scope creep.",
                    "Severe Monolithic Overload: Crams multiple sub-domains or distinct responsibilities into one file.",
                ],
            ),
            "importance": Score(
                instructions=(
                    "Assess the architectural permanence, generality, and security criticality of `candidate`."
                ),
                criteria=[
                    "Minor transient tip, cosmetic edge case, or localized styling nuance.",
                    "Standard idiomatic convention, common API pattern, or component rule.",
                    "Foundational architectural standard, core specification, critical security constraint.",
                ],
            ),
            "rule_effectiveness": Score(
                instructions=(
                    "Assess how strictly and universally binding this rule/instruction is intended to be applied "
                    "(enforceability, prescriptive authority, and mandatory status)."
                ),
                criteria=[
                    "Advisory / Contextual: Optional preference, suggestion, or contextual tip ('consider', 'may', flexible tips).",
                    "Recommended / Standard Default: Strong idiom or default convention.",
                    "Mandatory / Invariable Constraint: Non-negotiable directive, strict rule, or inviolable policy that must always be enforced.",
                ],
            ),
            "coherence": Score(
                instructions=(
                    "Evaluate `candidate.content` for Single Responsibility Principle (SRP) focus and "
                    "clean separation between Summary (Coarse View) and Detailed Rules (Fine View)."
                ),
                criteria=[
                    "Disparate concerns jumbled together; summary and rules are blurred or contradictory.",
                    "Generally focused on one topic, but with minor topical drift.",
                    "Laser-focused on a single responsibility with strict, clean division between Summary and Rules.",
                ],
            ),
            "grammar_and_clarity": Score(
                instructions=(
                    "Assess `candidate.content` for grammatical correctness, technical writing precision, and "
                    "absence of conversational filler or ambiguity."
                ),
                criteria=[
                    "Broken / Confusing: Grammatical errors, conversational chatter ('As an AI...', 'Sure!'), garbled sentences, or ambiguous phrasing.",
                    "Readable with Flaws: Understandable prose, but has minor grammatical slips, wordy explanations, or colloquial phrasing.",
                    "Exemplary Technical Clarity: Flawless grammar, crisp imperative tone, exact terminology, and unambiguous technical statements.",
                ],
            ),
            "markdown_format_quality": Score(
                instructions=(
                    "Evaluate `candidate.content` for markdown formatting quality, structural hierarchy, and clean documentation conventions."
                ),
                criteria=[
                    "Malformed / Unstructured: Wall of text, missing code language tags, broken lists, or chaotic heading levels.",
                    "Acceptable with Minor Flaws: Readable markdown, but has minor formatting inconsistencies.",
                    "Clean & Idiomatic: Well-formatted markdown with proper heading hierarchies, fenced code blocks with language identifiers, and consistent list indentation.",
                ],
            ),
            "practical_utility": Score(
                instructions=(
                    "Assess `candidate.content` for practical utility and applicability. Are directives concrete and verifiable? Are definitions, examples, "
                    "and specifications complete and precise?"
                ),
                criteria=[
                    "Abstract / Incomplete: Incomplete stubs lacking concrete directives or definitions.",
                    "Partially Useful: Helpful concepts, but leaves exact design/behavior ambiguous.",
                    "Complete: Either provides concrete, verifiable directives with code patterns (for rules) OR provides exhaustive, exact definitions and contracts (for references).",
                ],
            ),
            "is_coalescence_candidate": Noul(
                instructions=(
                    "Should `candidate` be folded into `parent` or merged with one of `siblings` "
                    "rather than existing as a standalone file?"
                ),
            ),
            "has_redundancy_or_conflict": Noul(
                instructions=(
                    "Does `candidate.content` duplicate rules already present in `parent` or `siblings`?"
                ),
            ),
        }

    async def check(
        self,
        candidate: KnowledgeCandidate,
        context: KnowledgeContext | None = None,
    ) -> JevAuditReport:
        """Audit a candidate knowledge node against taxonomy, sizing, and schema."""
        context = context or KnowledgeContext()

        # Step 1: Token counting & syntax validation
        raw_tokens = self.count_tokens(candidate.markdown)
        schema_errors = list(candidate.parse_errors)
        content_errors: list[str] = []

        # Deterministic markdown syntax check: unmatched code fences
        if candidate.markdown.count("```") % 2 != 0:
            content_errors.append("Unmatched code fence (odd count of '```')")

        if not candidate.frontmatter:
            schema_errors.append("Missing or invalid parsed frontmatter object")

        # Fast-fail on runtime type validation failure without calling Jev
        if schema_errors:
            size_status: SizeStatus = "undersized" if raw_tokens <= self.token_count_bound.lower_trigger else (
                "oversized" if raw_tokens >= self.token_count_bound.upper_trigger else "optimal"
            )
            return JevAuditReport(
                taxonomy_fit="optimal",
                token_count=raw_tokens,
                bloatedness_score=1.0,
                effective_token_count=float(raw_tokens),
                size_status=size_status,
                importance_score=0.5,
                content_importance=0.5,
                effectiveness_score=0.5,
                grammar_score=1.0,
                markdown_quality_score=1.0,
                practical_utility_score=1.0,
                content_errors=content_errors,
                schema_valid=False,
                schema_errors=schema_errors,
                verdict="REVISE_SCHEMA",
            )

        # Step 2: Build state payload
        state: dict[str, Any] = {
            "candidate": {
                "path": candidate.path,
                "token_count": raw_tokens,
                "frontmatter": candidate.frontmatter.model_dump(),
                "content": candidate.body,
            },
            "parent": context.parent.model_dump() if context.parent else None,
            "siblings": [s.model_dump() for s in context.siblings],
            "allowed_namespaces": context.allowed_namespaces,
        }

        # Step 3: Call TypeSafe Jev for semantic judgments
        questions = self._build_questions()

        if self.client:
            response = await self.client.system_one(state=state, questions=questions, model=self.model)
        else:
            async with AsyncTypeSafeClient() as client:
                response = await client.system_one(state=state, questions=questions, model=self.model)

        # Step 4: Extract typed answers
        taxonomy_choice: TaxonomyFit = getattr(response.choices["taxonomy_fit"], "choice", "optimal")  # type: ignore
        bloat_score: float = float(getattr(response.scores["bloatedness"], "score", 1.0))
        importance_score: float = round(float(getattr(response.scores["importance"], "score", 1.0)) / 2.0, 2)
        effectiveness_score: float = round(float(getattr(response.scores["rule_effectiveness"], "score", 1.0)) / 2.0, 2)
        coherence_score: float = round(float(getattr(response.scores["coherence"], "score", 2.0)) / 2.0, 2)
        grammar_score: float = round(float(getattr(response.scores["grammar_and_clarity"], "score", 2.0)) / 2.0, 2)
        markdown_quality_score: float = round(float(getattr(response.scores["markdown_format_quality"], "score", 2.0)) / 2.0, 2)
        practical_utility_score: float = round(float(getattr(response.scores["practical_utility"], "score", 2.0)) / 2.0, 2)
        is_coalescence_candidate: float = float(getattr(response.nouls["is_coalescence_candidate"], "noul", 0.0))
        has_redundancy: float = float(getattr(response.nouls["has_redundancy_or_conflict"], "noul", 0.0))

        # Damped max-blend: max(I, E) + lambda * min(I, E) * (1 - max(I, E)), lambda = 0.20
        c_max = max(importance_score, effectiveness_score)
        c_min = min(importance_score, effectiveness_score)
        final_importance = round(c_max + 0.20 * c_min * (1.0 - c_max), 2)

        # Step 5: Composite Effective Sizing & Dynamic Modulation (Section 3.4.1)
        # 1. Coherence Modulation of upper_bound_trigger: high coherence raises upper_bound_trigger (up to 2200)
        upper_trigger = self.token_count_bound.upper_trigger
        if coherence_score >= 0.80:
            upper_trigger += ((coherence_score - 0.80) / 0.20) * 400.0

        # 2. Sibling Diversity Modulation of lower_bound_trigger: high diversity (low redundancy) lowers lower_bound_trigger (down to 150-200)
        lower_trigger = self.token_count_bound.lower_trigger
        if context.siblings and has_redundancy < 0.20:
            lower_trigger -= ((0.20 - has_redundancy) / 0.20) * 100.0

        # EffectiveSize = RawTokens * (0.75 + 0.35 * BloatScore)
        effective_size = round(raw_tokens * (0.75 + 0.35 * bloat_score), 1)

        # Upper bound evaluates composite effective_size; Lower bound evaluates deterministic raw_tokens
        size_status: SizeStatus
        if effective_size >= upper_trigger:
            size_status = "oversized"
        elif raw_tokens <= lower_trigger:
            size_status = "undersized"
        else:
            size_status = "optimal"

        # Content quality verification
        if grammar_score < 0.40:
            content_errors.append(f"Grammar and clarity score below threshold ({grammar_score:.2f} < 0.40)")
        if markdown_quality_score < 0.40:
            content_errors.append(f"Markdown formatting quality score below threshold ({markdown_quality_score:.2f} < 0.40)")
        if practical_utility_score < 0.30:
            content_errors.append(f"Practical utility score below threshold ({practical_utility_score:.2f} < 0.30)")

        # Step 6: Determine Actionable Gate Verdict
        verdict: AuditVerdict
        merge_recommendation = "none"
        merge_candidate_siblings: list[str] = []

        if schema_errors:
            verdict = "REVISE_SCHEMA"
        elif content_errors:
            verdict = "REVISE_CONTENT"
        elif size_status == "oversized" or coherence_score < 0.40:
            verdict = "ESCALATE_REFACTOR"
        elif size_status == "undersized":
            if is_coalescence_candidate > 0.50:
                verdict = "MERGE_REQUIRED"
                merge_recommendation = "merge_into_sibling" if context.siblings else "fold_into_parent"
                merge_candidate_siblings = [s.path for s in context.siblings]
            elif final_importance < 0.30:
                # Delete / Prune low-importance unmergeable stubs (Section 3.4.2)
                verdict = "REVISE_SCHEMA"
            elif taxonomy_choice == "optimal" and has_redundancy < 0.35:
                # Retained as standalone leaf exception (Section 3.4.2)
                verdict = "PASS"
            else:
                verdict = "REVISE_SCHEMA"
        elif taxonomy_choice == "optimal" and has_redundancy < 0.35:
            verdict = "PASS"
        else:
            verdict = "REVISE_SCHEMA"

        token_count_bound = {
            "lower_trigger": self.token_count_bound.lower_trigger,
            "lower_target": self.token_count_bound.lower_target,
            "upper_target": self.token_count_bound.upper_target,
            "upper_trigger": self.token_count_bound.upper_trigger,
            "effective_lower_trigger": lower_trigger,
            "effective_upper_trigger": upper_trigger,
            "instruction": (
                f"Upper hysteresis triggered: Refactoring must split/group content, such that each child knowledge is under "
                f"({self.token_count_bound.upper_target:.0f} tokens) to ensure growth headroom."
                if size_status == "oversized"
                else (
                    f"Lower hysteresis triggered: Merging must coalesce with sibling or fold into parent knowledge, to reach at least "
                    f"({self.token_count_bound.lower_target:.0f} tokens) to prevent thrashing."
                    if size_status == "undersized"
                    else None
                )
            ),
        }

        return JevAuditReport(
            taxonomy_fit=taxonomy_choice,
            suggested_path=None if taxonomy_choice == "optimal" else "suggested_review",
            token_count=raw_tokens,
            bloatedness_score=bloat_score,
            effective_token_count=effective_size,
            size_status=size_status,
            importance_score=final_importance,
            content_importance=importance_score,
            effectiveness_score=effectiveness_score,
            merge_candidate_siblings=merge_candidate_siblings,
            merge_recommendation=merge_recommendation,  # type: ignore
            coherence_score=coherence_score,
            topic_drift_detected=(coherence_score < 0.60),
            grammar_score=grammar_score,
            markdown_quality_score=markdown_quality_score,
            practical_utility_score=practical_utility_score,
            content_errors=content_errors,
            redundancy_status="duplicate_conflict" if has_redundancy > 0.70 else (
                "partial_overlap" if has_redundancy > 0.35 else "novel"
            ),
            conflicting_paths=[s.path for s in context.siblings if has_redundancy > 0.35],
            schema_valid=(len(schema_errors) == 0),
            schema_errors=schema_errors,
            verdict=verdict,
            details={
                "is_coalescence_candidate_prob": is_coalescence_candidate,
                "has_redundancy_prob": has_redundancy,
                "token_count_bound": token_count_bound,
            },
        )

    def as_tool(self) -> Any:
        """Wrap the agent check logic into a callable tool for AutoGen or agents."""
        async def audit_knowledge(
            path: str,
            content: str,
            parent_path: str = "",
            sibling_paths: list[str] | None = None,
        ) -> dict[str, Any]:
            candidate = KnowledgeCandidate.from_markdown(path, content)
            from libhippo.models.knowledge import HubReference, SiblingReference
            ctx = KnowledgeContext(
                parent=HubReference(path=parent_path) if parent_path else None,
                siblings=[SiblingReference(path=p) for p in (sibling_paths or [])],
            )
            report = await self.check(candidate, ctx)
            return report.model_dump()

        return audit_knowledge
