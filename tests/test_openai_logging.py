"""Unit tests for OpenAI request/response logging and context hiding."""

from __future__ import annotations

import os
from typing import Any, AsyncIterator, Sequence
from unittest.mock import MagicMock
import pytest

from autogen_core.models import (
    AssistantMessage,
    ChatCompletionClient,
    CreateResult,
    LLMMessage,
    ModelCapabilities,
    ModelInfo,
    RequestUsage,
    SystemMessage,
    UserMessage,
)

from libhippo.models.logging_client import (
    LoggingChatCompletionClient,
    is_openai_logging_enabled,
    wrap_client_if_logging_enabled,
)


class MockInnerClient(ChatCompletionClient):
    """Mock ChatCompletionClient for testing wrapper."""

    def __init__(self, model: str = "gpt-4o") -> None:
        self.model = model
        self._info = ModelInfo(vision=True, function_calling=True, json_output=True, family="unknown")

    @property
    def model_info(self) -> ModelInfo:
        return self._info

    @property
    def capabilities(self) -> ModelCapabilities:
        return {"vision": True, "function_calling": True, "json_output": True}

    def actual_usage(self) -> RequestUsage:
        return RequestUsage(prompt_tokens=10, completion_tokens=5)

    def total_usage(self) -> RequestUsage:
        return RequestUsage(prompt_tokens=10, completion_tokens=5)

    def count_tokens(self, messages: Sequence[LLMMessage], tools: Sequence[Any] = []) -> int:
        return 15

    def remaining_tokens(self, messages: Sequence[LLMMessage], tools: Sequence[Any] = []) -> int:
        return 8000

    async def close(self) -> None:
        pass

    async def create(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[Any] = [],
        json_output: bool | None = None,
        extra_create_args: dict[str, Any] = {},
        cancellation_token: Any | None = None,
    ) -> CreateResult:
        return CreateResult(
            finish_reason="stop",
            content="Mocked answer to question",
            usage=RequestUsage(prompt_tokens=42, completion_tokens=12),
            cached=False,
        )

    async def create_stream(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[Any] = [],
        json_output: bool | None = None,
        extra_create_args: dict[str, Any] = {},
        cancellation_token: Any | None = None,
    ) -> AsyncIterator[Any]:
        yield "chunk 1"
        yield "chunk 2"
        yield CreateResult(
            finish_reason="stop",
            content="chunk 1chunk 2",
            usage=RequestUsage(prompt_tokens=42, completion_tokens=4),
            cached=False,
        )

    def custom_steer_method(self) -> str:
        return "steered"


def test_is_openai_logging_enabled_flag(monkeypatch: pytest.MonkeyPatch):
    """Verify is_openai_logging_enabled respects environment flags."""
    monkeypatch.delenv("LIBHIPPO_LOG_OPENAI", raising=False)
    monkeypatch.delenv("OPENAI_LOG", raising=False)
    monkeypatch.delenv("LOG_OPENAI", raising=False)
    assert not is_openai_logging_enabled()

    monkeypatch.setenv("LIBHIPPO_LOG_OPENAI", "1")
    assert is_openai_logging_enabled()

    monkeypatch.setenv("LIBHIPPO_LOG_OPENAI", "true")
    assert is_openai_logging_enabled()

    monkeypatch.delenv("LIBHIPPO_LOG_OPENAI")
    monkeypatch.setenv("OPENAI_LOG", "yes")
    assert is_openai_logging_enabled()


@pytest.mark.asyncio
async def test_logging_wrapper_create_hides_previous_context(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """Verify create hides prior context, logs counts, and shows latest message and response."""
    monkeypatch.setenv("LIBHIPPO_LOG_OPENAI", "1")

    inner = MockInnerClient(model="gpt-6.1-sol")
    wrapped = wrap_client_if_logging_enabled(inner)
    assert isinstance(wrapped, LoggingChatCompletionClient)

    # Delegation works
    assert wrapped.custom_steer_method() == "steered"
    assert wrapped.model == "gpt-6.1-sol"

    # Multi-turn messages
    messages = [
        SystemMessage(content="You are a senior developer."),
        UserMessage(content="First user question", source="user"),
        AssistantMessage(content="First assistant reply", source="assistant"),
        UserMessage(content="Second user request: please optimize", source="user"),
    ]

    import logging
    with caplog.at_level(logging.INFO, logger="libhippo.openai"):
        res = await wrapped.create(messages=messages)

    assert res.content == "Mocked answer to question"
    log_text = caplog.text

    # Verify request banner & context hiding
    assert "[OpenAI Request]" in log_text
    assert "Model: gpt-6.1-sol" in log_text
    assert "Context: 4 messages (3 previous hidden:" in log_text
    assert "First user question" not in log_text
    assert "First assistant reply" not in log_text
    assert "Latest Message" in log_text
    assert "Second user request: please optimize" in log_text

    # Verify response banner & content
    assert "[OpenAI Response]" in log_text
    assert "Finish: stop" in log_text
    assert "prompt=42, completion=12" in log_text
    assert "Mocked answer to question" in log_text


@pytest.mark.asyncio
async def test_logging_wrapper_create_stream(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """Verify create_stream logs stream request and final response."""
    monkeypatch.setenv("LIBHIPPO_LOG_OPENAI", "1")

    inner = MockInnerClient(model="gpt-6.1-sol")
    wrapped = LoggingChatCompletionClient(inner)

    messages = [UserMessage(content="Single turn prompt", source="user")]

    import logging
    with caplog.at_level(logging.INFO, logger="libhippo.openai"):
        chunks = []
        async for c in wrapped.create_stream(messages=messages):
            chunks.append(c)

    assert len(chunks) == 3
    log_text = caplog.text

    assert "[OpenAI Request (stream)]" in log_text
    assert "Single turn prompt" in log_text
    assert "[OpenAI Response (stream)]" in log_text


def test_logging_wrapper_disabled_by_default(monkeypatch: pytest.MonkeyPatch):
    """Verify that when the flag is disabled, client is not wrapped."""
    monkeypatch.delenv("LIBHIPPO_LOG_OPENAI", raising=False)
    monkeypatch.delenv("OPENAI_LOG", raising=False)
    monkeypatch.delenv("LOG_OPENAI", raising=False)

    inner = MockInnerClient()
    wrapped = wrap_client_if_logging_enabled(inner)
    assert wrapped is inner
