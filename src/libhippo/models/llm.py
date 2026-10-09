"""Centralized model configuration and client factory for OpenAI and TypeSafe Jev."""

from __future__ import annotations

import os
from typing import Any, Literal

from libhippo.config.models import (
    AgentRole,
    DEFAULT_AGENT_MODELS,
    ModelConfig,
)
from libhippo.models.decisions import OpenAIDecisionsClient
from libhippo.models.logging_client import wrap_client_if_logging_enabled


def format_cached_system_message(
    system_prompt: str,
    cache_system_prompt_only: bool = True,
) -> dict[str, Any]:
    """Format a system message with explicit prompt cache breakpoints.

    Supports OpenAI 'prompt_cache_breakpoint' and Anthropic 'cache_control'
    so only the static system prompt is written to cache (avoiding 1.25x write
    fees on volatile conversation turns).
    """
    if not cache_system_prompt_only:
        return {"role": "system", "content": system_prompt}
    return {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
                "prompt_cache_breakpoint": True,
            }
        ],
    }



class ModelRegistry:
    """Central registry and factory for agent model configurations and client instances."""

    def __init__(self) -> None:
        self._configs: dict[str, ModelConfig] = dict(DEFAULT_AGENT_MODELS) # type: ignore
        self._mock_clients: dict[str, Any] = {}

    def register(self, role_or_name: str, config: ModelConfig) -> None:
        """Register or override a model configuration for an agent role."""
        self._configs[role_or_name] = config

    def get_config(self, role_or_name: str) -> ModelConfig:
        """Get model configuration by agent role or name, supporting env overrides."""
        env_model = os.getenv(f"LIBHIPPO_{role_or_name.upper()}_MODEL")
        if role_or_name in self._configs:
            cfg = self._configs[role_or_name].model_copy()
            if env_model:
                cfg.model = env_model
            return cfg
        return ModelConfig(model=env_model or role_or_name)

    def set_mock_client(self, role_or_name: str, client: Any) -> None:
        """Inject a mock client for an agent role (used for testing)."""
        self._mock_clients[role_or_name] = client

    def get_mock_client(self, role_or_name: str) -> Any | None:
        """Get registered mock client if any."""
        return self._mock_clients.get(role_or_name)

    def clear_mocks(self) -> None:
        """Clear all registered mock clients."""
        self._mock_clients.clear()

    def create_chat_client(
        self,
        role_or_config: AgentRole | str | ModelConfig,
        **override_kwargs: Any,
    ) -> Any:
        """Create an AutoGen OpenAIChatCompletionClient for an agent role or config."""
        role_key = role_or_config if isinstance(role_or_config, str) else None
        if role_key and role_key in self._mock_clients:
            return self._mock_clients[role_key]

        config = (
            role_or_config
            if isinstance(role_or_config, ModelConfig)
            else self.get_config(str(role_or_config))
        )

        model_name = config.resolve_model_name()
        api_key = config.api_key or os.getenv("OPENAI_API_KEY") or "mock-key"
        base_url = config.base_url or os.getenv("OPENAI_BASE_URL")

        kwargs: dict[str, Any] = {
            "model": model_name,
            "api_key": api_key,
        }
        if base_url:
            kwargs["base_url"] = base_url
        if config.reasoning_effort is not None:
            kwargs["reasoning_effort"] = config.reasoning_effort
        if config.default_headers:
            kwargs["default_headers"] = config.default_headers

        kwargs.update(config.extra_kwargs)
        kwargs.update(override_kwargs)
        kwargs.pop("temperature", None)

        effective_model_info = (
            override_kwargs.get("model_info")
            or config.model_info
            or config.extra_kwargs.get("model_info")
        )
        if not effective_model_info:
            raise ValueError(f"model_info is required for model '{model_name}'.")
        kwargs["model_info"] = effective_model_info

        # Filter out None values
        filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}

        from libhippo.runner.transport import OpenAIResponsesClient # cyclic import if not placed here
        client = OpenAIResponsesClient(**filtered_kwargs)
        return wrap_client_if_logging_enabled(client, agent_role=str(role_key) if role_key else None)

    def create_decision_client(
        self,
        role_or_config: AgentRole | str | ModelConfig = "checker",
        **override_kwargs: Any,
    ) -> Any:
        """Create a decision client (AsyncTypeSafeClient or OpenAIDecisionsClient) for System One judgments."""
        role_key = role_or_config if isinstance(role_or_config, str) else None
        if role_key and role_key in self._mock_clients:
            return self._mock_clients[role_key]

        config = (
            role_or_config
            if isinstance(role_or_config, ModelConfig)
            else self.get_config(str(role_or_config))
        )

        model_name = override_kwargs.pop("model", None) or config.resolve_model_name()

        if config.provider == "openai":
            api_key = config.api_key or os.getenv("OPENAI_API_KEY")
            base_url = config.base_url or os.getenv("OPENAI_BASE_URL")

            kwargs: dict[str, Any] = {
                "model": model_name,
            }
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["base_url"] = base_url
            if config.default_headers:
                kwargs["default_headers"] = config.default_headers

            kwargs.update(config.extra_kwargs)
            kwargs.update(override_kwargs)
            return OpenAIDecisionsClient(**kwargs)

        # Fallback to TypeSafe Jev
        from typesafe_sdk import AsyncTypeSafeClient

        api_key = config.api_key or os.getenv("TYPESAFE_API_KEY")
        base_url = config.base_url or os.getenv("TYPESAFE_BASE_URL")

        kwargs = {
            "model": model_name,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url

        kwargs.update(config.extra_kwargs)
        kwargs.update(override_kwargs)

        return AsyncTypeSafeClient(**kwargs)


# Global default model registry instance
default_model_registry = ModelRegistry()


def get_model_config(role: AgentRole | str) -> ModelConfig:
    return default_model_registry.get_config(role)


def create_chat_client(role_or_config: AgentRole | str | ModelConfig, **kwargs: Any) -> Any:
    return default_model_registry.create_chat_client(role_or_config, **kwargs)


def create_decision_client(role_or_config: AgentRole | str | ModelConfig = "checker", **kwargs: Any) -> Any:
    return default_model_registry.create_decision_client(role_or_config, **kwargs)
