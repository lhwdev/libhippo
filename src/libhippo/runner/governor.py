"""Workload and token governor enforcing context budgets and safety circuits."""

from __future__ import annotations

from typing import Any

from libhippo.runner.config import HarnessConfig
from libhippo.runner.memory import ContextMemory


class MaxTurnsExceededError(RuntimeError):
    """Raised when conversation turns reach maximum limit."""


class CircuitBreakerTrippedError(RuntimeError):
    """Raised when consecutive tool failures trip emergency safety circuits."""


class WorkloadGovernor:
    """Monitors token usage, turn limits, and failure thresholds."""

    def __init__(self, config: HarnessConfig, memory: ContextMemory) -> None:
        self.config = config
        self.memory = memory
        self.current_turns: int = 0
        self._consecutive_failures: dict[str, int] = {}
        self.last_warning: str | None = None

    def start_turn(self) -> None:
        """Increment and verify turn ceiling before executing turn."""
        self.current_turns += 1
        if self.current_turns > self.config.max_turns:
            raise MaxTurnsExceededError(
                f"Maximum conversational turns ({self.config.max_turns}) exceeded."
            )

    def reset(self) -> None:
        """Reset turn counter and failure state."""
        self.current_turns = 0
        self._consecutive_failures.clear()
        self.last_warning = None

    def check_context_and_compact(self) -> dict[str, Any]:
        """Evaluate token watermarks and trigger lazy compaction if quota crossed."""
        total_tokens = self.memory.get_total_tokens()
        result: dict[str, Any] = {
            "total_tokens": total_tokens,
            "status": "ok",
            "evicted_tokens": 0,
        }

        if total_tokens >= self.config.hard_token_limit:
            evicted = self.memory.compact_zone3(self.config.compaction_target_tokens)
            result["status"] = "compacted"
            result["evicted_tokens"] = evicted
            result["post_compaction_tokens"] = self.memory.get_total_tokens()
        elif total_tokens >= self.config.soft_token_watermark:
            result["status"] = "soft_watermark_warning"
            self.last_warning = (
                f"Context crossed soft watermark ({total_tokens} >= {self.config.soft_token_watermark})."
            )

        return result

    def record_tool_result(self, tool_name: str, success: bool, error: str | None = None) -> None:
        """Track consecutive failures to trip circuit breaker on repeated errors."""
        key = tool_name
        if success:
            self._consecutive_failures[key] = 0
        else:
            count = self._consecutive_failures.get(key, 0) + 1
            self._consecutive_failures[key] = count
            if count >= 3:
                raise CircuitBreakerTrippedError(
                    f"Tool '{tool_name}' failed 3 consecutive times with error: {error}"
                )
