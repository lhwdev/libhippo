"""Unit tests for centralized ModelRegistry and client factories."""

from unittest.mock import MagicMock

from libhippo.models.llm import (
    ModelConfig,
    ModelRegistry,
    create_chat_client,
    create_typesafe_client,
    get_model_config,
)


def test_default_model_configs():
    """Verify default configurations across all 5 agent roles."""
    roles = ["task_solver", "book_keeper", "curator", "checker", "verifier"]
    for role in roles:
        cfg = get_model_config(role)
        assert cfg.model
        assert cfg.provider in ("openai", "typesafe")

    # Check specific architectural attributes
    solver_cfg = get_model_config("task_solver")
    assert solver_cfg.model == "gpt-6.1-sol"
    assert solver_cfg.cache_write is True

    bookkeeper_cfg = get_model_config("book_keeper")
    assert bookkeeper_cfg.model == "gpt-5-nano"
    assert bookkeeper_cfg.cache_write is False
    assert bookkeeper_cfg.temperature == 0.0

    curator_cfg = get_model_config("curator")
    assert curator_cfg.model == "gpt-6-luna"
    assert curator_cfg.temperature == 0.1

    verifier_cfg = get_model_config("verifier")
    assert verifier_cfg.model == "gpt-6.1-sol"
    assert verifier_cfg.cache_write is True
    assert verifier_cfg.temperature == 0.0

    checker_cfg = get_model_config("checker")
    assert checker_cfg.provider == "typesafe"
    assert checker_cfg.model == "jev"


def test_fallback_model_resolution(monkeypatch):
    """Verify fallback model resolution when LIBHIPPO_USE_FALLBACK_MODELS=1."""
    cfg = ModelConfig(model="gpt-5-nano", fallback_model="gpt-6-luna")
    assert cfg.resolve_model_name() == "gpt-5-nano"

    monkeypatch.setenv("LIBHIPPO_USE_FALLBACK_MODELS", "1")
    assert cfg.resolve_model_name() == "gpt-6-luna"


def test_environment_override(monkeypatch):
    """Verify role model override via environment variables."""
    registry = ModelRegistry()
    monkeypatch.setenv("LIBHIPPO_TASK_SOLVER_MODEL", "custom-solver-model")
    cfg = registry.get_config("task_solver")
    assert cfg.model == "custom-solver-model"


def test_mock_client_injection():
    """Verify injecting mock clients into ModelRegistry."""
    registry = ModelRegistry()
    mock_chat = MagicMock()
    mock_typesafe = MagicMock()

    registry.set_mock_client("book_keeper", mock_chat)
    registry.set_mock_client("checker", mock_typesafe)

    assert registry.create_chat_client("book_keeper") is mock_chat
    assert registry.create_typesafe_client("checker") is mock_typesafe

    registry.clear_mocks()
    assert registry.get_mock_client("book_keeper") is None


def test_create_chat_client_instantiation():
    """Verify create_chat_client returns OpenAIChatCompletionClient with correct parameters."""
    client = create_chat_client(
        "book_keeper",
        api_key="test-api-key",
        model="gpt-5-nano",
    )
    from autogen_ext.models.openai import OpenAIChatCompletionClient

    assert isinstance(client, OpenAIChatCompletionClient)


def test_create_typesafe_client_instantiation():
    """Verify create_typesafe_client returns AsyncTypeSafeClient with correct model."""
    client = create_typesafe_client("checker", api_key="test-api-key")
    from typesafe_sdk import AsyncTypeSafeClient

    assert isinstance(client, AsyncTypeSafeClient)


def test_format_cached_system_message():
    """Verify format_cached_system_message formats explicit prompt cache breakpoints."""
    from libhippo.models.llm import format_cached_system_message

    msg_uncached = format_cached_system_message("Prompt text", cache_system_prompt_only=False)
    assert msg_uncached == {"role": "system", "content": "Prompt text"}

    msg_cached = format_cached_system_message("Prompt text", cache_system_prompt_only=True)
    assert msg_cached["role"] == "system"
    assert isinstance(msg_cached["content"], list)
    block = msg_cached["content"][0]
    assert block["type"] == "text"
    assert block["text"] == "Prompt text"
    assert block["prompt_cache_breakpoint"] is True
    assert block["cache_control"] == {"type": "ephemeral"}

