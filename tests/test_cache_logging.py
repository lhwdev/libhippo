"""Tests for prompt cache extraction, logging, and turn telemetry."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from autogen_core.models import CreateResult, RequestUsage

from libhippo.models.logging_client import _log_response
from libhippo.runner.transport import OpenAIResponsesClient
from libhippo.runner.types import TurnCompletedEvent


def test_parse_response_extracts_cache_and_reasoning_tokens() -> None:
    client = OpenAIResponsesClient(model="gpt-6.1-sol", api_key="test-key")

    mock_resp = SimpleNamespace(
        id="resp-123",
        output=[],
        output_text="Hello world",
        usage=SimpleNamespace(
            input_tokens=2048,
            output_tokens=350,
            input_tokens_details=SimpleNamespace(
                cached_tokens=1920,
                cache_write_tokens=0,
            ),
            output_tokens_details=SimpleNamespace(
                reasoning_tokens=128,
            ),
        ),
    )

    res = client._parse_response(mock_resp)
    assert isinstance(res, CreateResult)
    assert res.cached is True
    assert res.usage.prompt_tokens == 2048
    assert res.usage.completion_tokens == 350
    assert getattr(res.usage, "cached_tokens", 0) == 1920
    assert getattr(res.usage, "cache_write_tokens", 0) == 0
    assert getattr(res.usage, "reasoning_tokens", 0) == 128


def test_parse_response_with_cache_write_tokens() -> None:
    client = OpenAIResponsesClient(model="gpt-6.1-sol", api_key="test-key")

    mock_resp = SimpleNamespace(
        id="resp-124",
        output=[],
        output_text="Fresh write",
        usage=SimpleNamespace(
            input_tokens=1024,
            output_tokens=100,
            input_tokens_details=SimpleNamespace(
                cached_tokens=0,
                cache_write_tokens=1024,
            ),
            output_tokens_details=SimpleNamespace(
                reasoning_tokens=0,
            ),
        ),
    )

    res = client._parse_response(mock_resp)
    assert res.cached is False
    assert getattr(res.usage, "cached_tokens", 0) == 0
    assert getattr(res.usage, "cache_write_tokens", 0) == 1024


def test_log_response_formats_cache_hit_and_reasoning(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("LIBHIPPO_LOG_OPENAI", "1")

    usage = RequestUsage(prompt_tokens=2048, completion_tokens=300)
    setattr(usage, "cached_tokens", 1920)
    setattr(usage, "cache_write_tokens", 0)
    setattr(usage, "reasoning_tokens", 128)

    res = CreateResult(
        finish_reason="stop",
        content="Final code",
        usage=usage,
        cached=True,
    )

    mock_client = MagicMock()
    mock_client.model = "gpt-6.1-sol"

    with caplog.at_level(logging.INFO, logger="libhippo.openai"):
        _log_response(mock_client, res)

    log_text = caplog.text
    assert "Cache: HIT (1920/2048, 93.8%)" in log_text
    assert "Reasoning: 128 tokens" in log_text


def test_turn_completed_event_holds_cache_telemetry() -> None:
    evt = TurnCompletedEvent(
        turn_index=1,
        total_tokens=2500,
        duration_seconds=1.5,
        response="Complete",
        cached_tokens=1920,
        cache_hit_rate=0.9375,
    )
    assert evt.cached_tokens == 1920
    assert evt.cache_hit_rate == 0.9375


def test_log_request_and_response_includes_agent_role(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    from autogen_core.models import UserMessage
    from libhippo.models.logging_client import _log_request, _log_response

    monkeypatch.setenv("LIBHIPPO_LOG_OPENAI", "1")

    mock_client = MagicMock()
    mock_client.model = "gpt-5-nano"
    mock_client.agent_role = "BookKeeperAgent"

    with caplog.at_level(logging.INFO, logger="libhippo.openai"):
        _log_request(mock_client, [UserMessage(content="Query index", source="user")])
        res = CreateResult(finish_reason="stop", content="Found", usage=RequestUsage(prompt_tokens=100, completion_tokens=50), cached=False)
        _log_response(mock_client, res)

    assert "Model: gpt-5-nano [Agent: BookKeeperAgent]" in caplog.text
