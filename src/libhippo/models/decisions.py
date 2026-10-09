"""OpenAI Decisions API client adapter.

Provides a drop-in replacement for TypeSafe Jev System One judgments using
OpenAI's Decisions API (powered by gpt-6-luna).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class OpenAIDecisionsError(RuntimeError):
    """Exception raised when OpenAI Decisions API fails."""


@dataclass
class ChoiceResult:
    choice: str
    confidence: float = 1.0


@dataclass
class NoulResult:
    noul: float


@dataclass
class ScoreResult:
    score: float


@dataclass
class DecisionResponse:
    """Unified response object compatible with TypeSafe SystemOneResponse."""

    choices: dict[str, ChoiceResult]
    nouls: dict[str, NoulResult]
    scores: dict[str, ScoreResult]
    raw_response: dict[str, Any] | None = None


class OpenAIDecisionsClient:
    """Client for OpenAI Decisions API adhering to TypeSafeClientProtocol.

    Translates TypeSafe Choice, Noul, and Score questions into the OpenAI Decisions
    schema and calls POST /v1/decisions with gpt-6-luna.
    """

    def __init__(
        self,
        model: str = "gpt-6-luna",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        default_headers: dict[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise OpenAIDecisionsError("No OpenAI API key provided. Set OPENAI_API_KEY or pass api_key.")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout
        self.default_headers = default_headers or {}
        self._http_client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> OpenAIDecisionsClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self.timeout)
            self._owns_client = True
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self.timeout)
            self._owns_client = True
        return self._http_client

    def _translate_questions(self, questions: dict[str, Any]) -> list[dict[str, Any]]:
        """Translate TypeSafe question primitives into OpenAI Decisions API questions."""
        translated: list[dict[str, Any]] = []

        for name, q in questions.items():
            instructions = getattr(q, "instructions", "") or (q.get("instructions", "") if isinstance(q, dict) else "")
            q_type = (getattr(q, "type", None) or (q.get("type") if isinstance(q, dict) else None) or type(q).__name__.lower())

            if q_type in ("noul", "predicate"):
                translated.append({
                    "type": "predicate",
                    "name": name,
                    "instructions": instructions,
                })
            elif q_type == "score" or hasattr(q, "levels") or (isinstance(q, dict) and "levels" in q) or (hasattr(q, "criteria") and isinstance(q.criteria, list)):
                raw_levels = getattr(q, "levels", None) or getattr(q, "criteria", None) or (q.get("levels", q.get("criteria", {})) if isinstance(q, dict) else {})
                levels_list: list[dict[str, Any]] = []
                if isinstance(raw_levels, dict):
                    for score_val, desc in raw_levels.items():
                        try:
                            num_score = float(score_val)
                        except (ValueError, TypeError):
                            num_score = float(len(levels_list))
                        levels_list.append({"score": num_score, "description": str(desc)})
                elif isinstance(raw_levels, list):
                    for idx, item in enumerate(raw_levels):
                        if isinstance(item, dict):
                            levels_list.append(item)
                        else:
                            levels_list.append({"score": float(idx), "description": str(item)})
                translated.append({
                    "type": "score",
                    "name": name,
                    "instructions": instructions,
                    "levels": levels_list,
                })
            else:
                raw_criteria = getattr(q, "criteria", None) or (q.get("criteria", {}) if isinstance(q, dict) else {})
                choices_list: list[dict[str, str]] = []
                if isinstance(raw_criteria, dict):
                    for val, desc in raw_criteria.items():
                        choices_list.append({"value": str(val), "description": str(desc)})
                elif isinstance(raw_criteria, list):
                    for item in raw_criteria:
                        if isinstance(item, dict):
                            choices_list.append(item)
                        else:
                            choices_list.append({"value": str(item), "description": str(item)})
                translated.append({
                    "type": "choice",
                    "name": name,
                    "instructions": instructions,
                    "choices": choices_list,
                })

        return translated

    def _parse_response(self, data: dict[str, Any]) -> DecisionResponse:
        """Parse raw Decisions API JSON into DecisionResponse."""
        choices: dict[str, ChoiceResult] = {}
        nouls: dict[str, NoulResult] = {}
        scores: dict[str, ScoreResult] = {}

        answers = data.get("answers", [])
        if isinstance(answers, dict):
            # Dict form: {"question_name": {...}}
            items = answers.items()
        elif isinstance(answers, list):
            # List form: [{"name": "q1", ...}]
            items = [(a.get("name", f"q_{i}"), a) for i, a in enumerate(answers) if isinstance(a, dict)]
        else:
            items = []

        for name, ans in items:
            ans_type = ans.get("type")
            if ans_type == "choice" or "choice" in ans:
                choice_val = str(ans.get("choice", ""))
                conf = float(ans.get("confidence", 1.0))
                choices[name] = ChoiceResult(choice=choice_val, confidence=conf)
            elif ans_type == "score" or "score" in ans:
                score_val = float(ans.get("score", 0.0))
                scores[name] = ScoreResult(score=score_val)
            else:
                prob = float(ans.get("probability", ans.get("noul", ans.get("predicate", 0.0))))
                nouls[name] = NoulResult(noul=prob)

        return DecisionResponse(choices=choices, nouls=nouls, scores=scores, raw_response=data)

    async def system_one(
        self,
        *,
        state: dict[str, Any],
        questions: dict[str, Any],
        model: str | None = None,
        **kwargs: Any,
    ) -> DecisionResponse:
        """Execute semantic judgments against OpenAI Decisions API (POST /v1/decisions)."""
        effective_model = model or self.model
        client = self._get_http_client()
        url = f"{self.base_url}/decisions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.default_headers,
        }

        payload: dict[str, Any] = {
            "model": effective_model,
            "input": state,
            "questions": self._translate_questions(questions),
        }

        logger.debug("Dispatching request to OpenAI Decisions API at %s", url)

        try:
            response = await client.post(url, json=payload, headers=headers)
        except Exception as e:
            raise OpenAIDecisionsError(f"Failed to connect to OpenAI Decisions API at {url}: {e}") from e

        if response.status_code != 200:
            raise OpenAIDecisionsError(
                f"OpenAI Decisions API returned HTTP {response.status_code}: {response.text}"
            )

        try:
            data = response.json()
        except Exception as e:
            raise OpenAIDecisionsError(f"Failed to parse OpenAI Decisions JSON response: {e}") from e

        return self._parse_response(data)
