"""KnowledgeHarvestObserver: TypeSafe Jev System One observer detecting novel context learnings."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from typesafe_sdk import Choice, Noul

from libhippo.agents.base import BaseHippoAgent
from libhippo.models.llm import create_decision_client, default_model_registry, get_model_config

logger = logging.getLogger(__name__)

KnowledgeScope = Literal["project", "common", "user"]
KnowledgeNature = Literal["critical_rule", "foundation", "transient_tip"]


@dataclass
class HarvestEvaluation:
    """Outcome of TypeSafe Jev evaluation on whether turn context holds permanent knowledge."""

    should_harvest: bool
    novelty_probability: float
    is_ambiguous: bool = False
    ambiguity_probability: float = 0.0
    target_scope: KnowledgeScope = "project"
    knowledge_nature: KnowledgeNature = "critical_rule"
    topic_hint: str = ""
    rationale: str = ""


class KnowledgeHarvestObserver(BaseHippoAgent):
    """Zero-cost / fast semantic gatekeeper evaluating turns for novel knowledge."""

    def __init__(
        self,
        name: str = "KnowledgeHarvestObserver",
        description: str = "Evaluates whether completed coding turns discovered reusable engineering knowledge.",
        client: Any | None = None,
        model: str | None = None,
        threshold: float = 0.70,
        ambiguity_threshold: float = 0.70,
    ) -> None:
        super().__init__(name=name, description=description)
        cfg = get_model_config("checker")
        self.client = client
        self.model = model or cfg.resolve_model_name()
        self.threshold = threshold
        self.ambiguity_threshold = ambiguity_threshold

    def _build_questions(self) -> dict[str, Any]:
        """Construct TypeSafe Jev System One questions for knowledge detection."""
        return {
            "has_novel_learnings": Noul(
                instructions=(
                    "Did this completed software engineering turn or task execution uncover a novel "
                    "repository-specific pattern, non-obvious bug resolution, framework gotcha, "
                    "or user preference worth preserving in the permanent knowledge base?"
                ),
            ),
            "is_ambiguous": Noul(
                instructions=(
                    "Is there ambiguity or uncertainty where clipped tool executions input/output SEEM IMPORTANT "
                    "and might contain gotchas or discoveries warranting deeper inspection?"
                ),
            ),
            "target_scope": Choice(
                instructions="Which knowledge namespace should this learning be recorded under?",
                criteria={
                    "project": "Specific to this repository's architecture, setup, commands, or code conventions.",
                    "common": "General language or framework standard applicable across multiple repositories.",
                    "user": "User's personal coding preference, authoring style, or editor conventions.",
                },
            ),
            "knowledge_nature": Choice(
                instructions="What is the permanence and operational nature of this knowledge?",
                criteria={
                    "critical_rule": "Strict rule, API contract, or bug gotcha that must be followed to avoid failure.",
                    "foundation": "Core architectural setup, base idioms, or directory structure.",
                    "transient_tip": "Minor ad-hoc tip, transient workaround, or single-use detail.",
                },
            ),
        }

    async def evaluate(self, state: dict[str, Any]) -> HarvestEvaluation:
        """Evaluate session/turn state using TypeSafe Jev semantic inference."""
        questions = self._build_questions()

        client = self.client or default_model_registry.get_mock_client("harvest_observer") or default_model_registry.get_mock_client("checker")
        try:
            if client:
                response = await client.system_one(state=state, questions=questions, model=self.model)
            else:
                async with create_decision_client("checker", model=self.model) as decision_client:
                    response = await decision_client.system_one(state=state, questions=questions, model=self.model)
        except Exception as e:
            logger.warning(f"KnowledgeHarvestObserver evaluation skipped due to error: {e}")
            return HarvestEvaluation(
                should_harvest=False,
                novelty_probability=0.0,
                is_ambiguous=False,
                ambiguity_probability=0.0,
                target_scope="project",
                knowledge_nature="transient_tip",
                rationale=f"Evaluation failed: {e}",
            )

        nouls = response.nouls if hasattr(response, "nouls") and isinstance(response.nouls, dict) else {}
        novelty_obj = nouls.get("has_novel_learnings")
        novelty_prob = float(getattr(novelty_obj, "noul", 0.0)) if novelty_obj else 0.0

        ambiguity_obj = nouls.get("is_ambiguous")
        ambiguity_prob = float(getattr(ambiguity_obj, "noul", 0.0)) if ambiguity_obj else 0.0

        choices = response.choices if hasattr(response, "choices") and isinstance(response.choices, dict) else {}
        scope_choice = choices.get("target_scope")
        target_scope: KnowledgeScope = getattr(scope_choice, "choice", "project") if scope_choice else "project"  # type: ignore

        nature_choice = choices.get("knowledge_nature")
        knowledge_nature: KnowledgeNature = getattr(nature_choice, "choice", "critical_rule") if nature_choice else "critical_rule"  # type: ignore

        is_novel = novelty_prob >= self.threshold
        is_ambiguous = ambiguity_prob >= self.ambiguity_threshold
        should_harvest = is_novel or is_ambiguous

        rationale_items = []
        if is_novel:
            rationale_items.append(f"novelty {novelty_prob:.2f} >= {self.threshold}")
        if is_ambiguous:
            rationale_items.append(f"ambiguous tool signals {ambiguity_prob:.2f} >= {self.ambiguity_threshold}")
        rationale = f"Triggered harvest: {', '.join(rationale_items)}" if rationale_items else f"Bypassed: routine turn (novelty {novelty_prob:.2f}, ambiguity {ambiguity_prob:.2f})"

        return HarvestEvaluation(
            should_harvest=should_harvest,
            novelty_probability=novelty_prob,
            is_ambiguous=is_ambiguous,
            ambiguity_probability=ambiguity_prob,
            target_scope=target_scope,
            knowledge_nature=knowledge_nature,
            rationale=rationale,
        )
