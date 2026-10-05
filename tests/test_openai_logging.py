"""Unit tests for OpenAI request/response logging, system prompt on first send, and context hiding."""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Sequence
import pytest

from autogen_core.models import (
    AssistantMessage,
    ChatCompletionClient,
    CreateResult,
    FunctionExecutionResult,
    FunctionExecutionResultMessage,
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
    monkeypatch.setattr("libhippo.models.logging_client._env_loaded", True)
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
async def test_system_prompt_shown_on_first_send_and_hidden_on_subsequent(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """Verify system prompt is shown on first send and hidden as previous context on turn 2."""
    monkeypatch.setenv("LIBHIPPO_LOG_OPENAI", "1")

    inner = MockInnerClient(model="gpt-6.1-sol")
    wrapped = wrap_client_if_logging_enabled(inner)
    assert isinstance(wrapped, LoggingChatCompletionClient)

    sys_prompt = "You are a senior coding agent with strict architectural standards."

    # Turn 1: First send with SystemMessage and UserMessage
    turn1_messages = [
        SystemMessage(content=sys_prompt),
        UserMessage(content="Turn 1: Fix bug in parser", source="user"),
    ]

    with caplog.at_level(logging.INFO, logger="libhippo.openai"):
        caplog.clear()
        res1 = await wrapped.create(messages=turn1_messages)
        assert res1.content == "Mocked answer to question"
        log1 = caplog.text

    # Turn 1 should show the system prompt explicitly
    assert "--- System Prompt (First Send) ---" in log1
    assert sys_prompt in log1
    assert "Turn 1: Fix bug in parser" in log1
    assert "system prompt shown" in log1

    # Turn 2: Subsequent send with prior history + new request
    turn2_messages = [
        SystemMessage(content=sys_prompt),
        UserMessage(content="Turn 1: Fix bug in parser", source="user"),
        AssistantMessage(content="Mocked answer to question", source="assistant"),
        UserMessage(content="Turn 2: Add test cases", source="user"),
    ]

    with caplog.at_level(logging.INFO, logger="libhippo.openai"):
        caplog.clear()
        res2 = await wrapped.create(messages=turn2_messages)
        assert res2.content == "Mocked answer to question"
        log2 = caplog.text

    # Turn 2 should NOT show the system prompt again
    assert "--- System Prompt (First Send) ---" not in log2
    assert "Turn 2: Add test cases" in log2
    # Prior history should be hidden with count breakdown
    assert "previous hidden: 1 SystemMessage, 1 UserMessage, 1 AssistantMessage" in log2
    assert "Turn 1: Fix bug in parser" not in log2


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


@pytest.mark.asyncio
async def test_tool_output_truncation(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """Verify tool output longer than 1500 chars is truncated in logs."""
    monkeypatch.setenv("LIBHIPPO_LOG_OPENAI", "1")
    inner = MockInnerClient()
    wrapped = LoggingChatCompletionClient(inner)

    long_output = "x" * 2500
    messages = [
        UserMessage(content="Run tool", source="user"),
        FunctionExecutionResultMessage(
            content=[FunctionExecutionResult(call_id="call_1", content=long_output, name="test_tool")]
        ),
    ]

    with caplog.at_level(logging.INFO, logger="libhippo.openai"):
        await wrapped.create(messages=messages)

    log_text = caplog.text
    assert "... [truncated 1000 characters] ..." in log_text


def test_file_handler_logging(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Verify logs are written to local log file configured by LIBHIPPO_LOG_FILE."""
    log_file = tmp_path / "test_libhippo.log"
    monkeypatch.setenv("LIBHIPPO_LOG_FILE", str(log_file))
    monkeypatch.setenv("LIBHIPPO_LOG_OPENAI", "1")
    monkeypatch.setattr("libhippo.models.logging_client._file_handler_initialized", False)

    from libhippo.models.logging_client import _setup_file_handler, logger
    _setup_file_handler()

    logger.info("Test log entry to file")
    assert log_file.exists()
    assert "Test log entry to file" in log_file.read_text(encoding="utf-8")

