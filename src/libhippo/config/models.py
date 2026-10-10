"""Centralized model configuration definitions for LibHippo.

Modify all model choices, reasoning efforts, cache behaviors, and decision
providers (TypeSafe Jev vs. OpenAI Decisions) in this file.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, Field

AgentRole = Literal["task_solver", "book_keeper", "curator", "checker", "verifier"]
ModelProvider = Literal["openai", "typesafe"]


class ModelConfig(BaseModel):
    """Configuration specification for an agent language/judgment model."""

    provider: ModelProvider = "openai"
    model: str
    fallback_model: str | None = None
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    cache_write: bool = False
    cache_mode: Literal["auto", "explicit", "off"] = "auto"
    cache_system_prompt_only: bool = False
    prompt_cache_key: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    default_headers: dict[str, str] = Field(default_factory=dict)
    extra_kwargs: dict[str, Any] = Field(default_factory=dict)
    model_info: dict[str, Any] | None = None

    def resolve_model_name(self) -> str:
        """Resolve effective model name using fallback if configured or in test mode."""
        use_fallback = os.getenv("LIBHIPPO_USE_FALLBACK_MODELS", "0") in ("1", "true", "True")
        target = self.fallback_model if use_fallback and self.fallback_model else self.model
        if self.provider == "typesafe" and target == "jev":
            return "jev-latest"
        return target


DEFAULT_OPENAI_MODEL_INFO: dict[str, Any] = {
    "vision": True,
    "function_calling": True,
    "json_output": True,
    "family": "unknown",
    "structured_output": True,
    "multiple_system_messages": True,
}


# Predefined constant reasoning efforts for specialized modes
DEFAULT_HARVEST_REASONING_EFFORT: Literal["none", "minimal", "low", "medium", "high"] = "low"
DEFAULT_DEEP_EXPLORATION_REASONING_EFFORT: Literal["none", "minimal", "low", "medium", "high"] = "medium"


DEFAULT_TASK_SOLVER_CONFIG = ModelConfig(
    provider="openai",
    model="gpt-6.1-sol",
    reasoning_effort="medium",
    cache_write=True,
    cache_mode="auto",
    cache_system_prompt_only=False,
    model_info=DEFAULT_OPENAI_MODEL_INFO,
)

DEFAULT_BOOK_KEEPER_CONFIG = ModelConfig(
    provider="typesafe",
    model="jev-latest",
)

# DEFAULT_BOOK_KEEPER_CONFIG = ModelConfig(
#     provider="openai",
#     model="gpt-6-luna",
# )

DEFAULT_CURATOR_CONFIG = ModelConfig(
    provider="openai",
    model="gpt-6-luna",
    reasoning_effort="medium",
    cache_write=False,
    cache_mode="explicit",
    cache_system_prompt_only=False,
    prompt_cache_key="libhippo-curator",
    model_info=DEFAULT_OPENAI_MODEL_INFO,
)

DEFAULT_CHECKER_CONFIG = ModelConfig(
    provider="typesafe",
    model="jev-latest",
)

DEFAULT_VERIFIER_CONFIG = ModelConfig(
    provider="openai",
    model="gpt-6.1-sol",
    reasoning_effort="medium",
    cache_write=True,
    cache_mode="auto",
    cache_system_prompt_only=False,
    model_info=DEFAULT_OPENAI_MODEL_INFO,
)

DEFAULT_AGENT_MODELS: dict[AgentRole, ModelConfig] = {
    "task_solver": DEFAULT_TASK_SOLVER_CONFIG,
    "book_keeper": DEFAULT_BOOK_KEEPER_CONFIG,
    "curator": DEFAULT_CURATOR_CONFIG,
    "checker": DEFAULT_CHECKER_CONFIG,
    "verifier": DEFAULT_VERIFIER_CONFIG,
}

# Default model used by the general agent harness
DEFAULT_HARNESS_MODEL = DEFAULT_TASK_SOLVER_CONFIG.model
