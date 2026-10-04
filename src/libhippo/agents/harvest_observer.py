"""KnowledgeHarvestObserver: TypeSafe Jev System One observer detecting novel context learnings."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from typesafe_sdk import Choice, Noul

from libhippo.agents.base import BaseHippoAgent
from libhippo.models.llm import create_typesafe_client, default_model_registry, get_model_config

logger = logging.getLogger(__name__)

KnowledgeScope = Literal["project", "common", "user"]
KnowledgeNature = Literal["critical_rule", "foundation", "transient_tip"]


@dataclass
class HarvestEvaluation:
    """Outcome of TypeSafe Jev evaluation on whether turn context holds permanent knowledge."""

    should_harvest: bool
    novelty_probability: float
    target_scope: KnowledgeScope
    knowledge_nature: KnowledgeNature
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
    ) -> None:
        super().__init__(name=name, description=description)
        cfg = get_model_config("checker")
        self.client = client
        self.model = model or cfg.resolve_model_name()
        self.threshold = threshold

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
                async with create_typesafe_client("checker", model=self.model) as typesafe_client:
                    response = await typesafe_client.system_one(state=state, questions=questions, model=self.model)
        except Exception as e:
            logger.warning(f"KnowledgeHarvestObserver evaluation skipped due to error: {e}")
            return HarvestEvaluation(
                should_harvest=False,
                novelty_probability=0.0,
                target_scope="project",
                knowledge_nature="transient_tip",
                rationale=f"Evaluation failed: {e}",
            )

        novelty_prob = float(getattr(response.nouls["has_novel_learnings"], "noul", 0.0))
        target_scope: KnowledgeScope = getattr(response.choices["target_scope"], "choice", "project")  # type: ignore
        knowledge_nature: KnowledgeNature = getattr(response.choices["knowledge_nature"], "choice", "critical_rule")  # type: ignore

        should_harvest = novelty_prob >= self.threshold

        return HarvestEvaluation(
            should_harvest=should_harvest,
            novelty_probability=novelty_prob,
            target_scope=target_scope,
            knowledge_nature=knowledge_nature,
            rationale=f"Evaluated with novelty probability {novelty_prob:.2f} (threshold: {self.threshold})",
        )
