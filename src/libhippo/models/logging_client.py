"""Transparent logging wrapper for ChatCompletionClient instances."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from typing import Any, AsyncIterator, Sequence

from autogen_core.models import (
    ChatCompletionClient,
    CreateResult,
    LLMMessage,
    ModelCapabilities,
    ModelInfo,
    RequestUsage,
)

from libhippo.runner.env import load_env_hierarchy

# Ensure env files (including .env.development.local) are loaded
load_env_hierarchy()

logger = logging.getLogger("libhippo.openai")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
logger.propagate = False


def is_openai_logging_enabled() -> bool:
    """Check if OpenAI request/response logging is enabled via environment."""
    val = (
        os.getenv("LIBHIPPO_LOG_OPENAI")
        or os.getenv("OPENAI_LOG")
        or os.getenv("LOG_OPENAI")
        or ""
    ).strip().lower()
    return val in ("1", "true", "yes", "on", "enable", "enabled")


def _format_message(msg: Any) -> tuple[str, str]:
    if isinstance(msg, dict):
        role = msg.get("role", "message")
        content = msg.get("content", "")
    elif hasattr(msg, "content"):
        role = getattr(msg, "type", type(msg).__name__)
        source = getattr(msg, "source", None)
        if source and source.lower() not in role.lower():
            role = f"{role}:{source}"
        content = msg.content
    else:
        role = type(msg).__name__
        content = str(msg)

    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or part))
            elif hasattr(part, "name") and hasattr(part, "arguments"):
                parts.append(f"{part.name}({part.arguments})")
            elif hasattr(part, "call_id") and hasattr(part, "content"):
                part_name = getattr(part, "name", "tool")
                parts.append(f"[{part_name}:{part.call_id}] {part.content}")
            elif hasattr(part, "content"):
                parts.append(str(part.content))
            else:
                parts.append(str(part))
        text = "\n".join(parts)
    else:
        text = str(content)
    return role, text


def _log_request(
    client: Any,
    messages: Sequence[LLMMessage] | Sequence[Any],
    tools: Sequence[Any] = [],
    stream: bool = False,
) -> None:
    if not is_openai_logging_enabled():
        return

    model_name = getattr(client, "model", None) or getattr(client, "_model", None) or "openai"
    total_messages = len(messages)

    # Track system prompts logged for this client so they are only displayed on first send
    logged_system_prompts: set[str] = getattr(client, "_logged_system_prompts", None)  # type: ignore
    if logged_system_prompts is None:
        logged_system_prompts = set()
        try:
            client._logged_system_prompts = logged_system_prompts
        except Exception:
            pass

    # Extract all system messages from the request
    sys_msgs: list[tuple[Any, str]] = []
    for m in messages:
        role, text = _format_message(m)
        if "system" in role.lower():
            sys_msgs.append((m, text))

    shown_sys_msgs: list[tuple[Any, str]] = []
    for sm, text in sys_msgs:
        h = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        if h not in logged_system_prompts:
            shown_sys_msgs.append((sm, text))
            logged_system_prompts.add(h)

    stream_tag = " (stream)" if stream else ""
    lines: list[str] = [
        f"{'=' * 38} [OpenAI Request{stream_tag}] {'=' * 38}",
        f"Model: {model_name}",
    ]

    # Show system prompt(s) on first send
    for _, text in shown_sys_msgs:
        lines.append("--- System Prompt (First Send) ---")
        lines.append(text)

    if total_messages == 0:
        lines.append("Context: 0 messages")
    elif total_messages == 1:
        if not shown_sys_msgs:
            role, text = _format_message(messages[0])
            lines.append("Context: 1 message (0 previous hidden)")
            lines.append(f"--- Latest Message [{role}] ---")
            lines.append(text)
        else:
            lines.append("Context: 1 message (system prompt shown above)")
    else:
        # Collect all trailing tool messages as latest turn if multiple tool messages are at the end
        latest_msgs: list[Any] = [messages[-1]]
        last_role, _ = _format_message(messages[-1])
        if "tool" in last_role.lower() or "functionexecution" in last_role.lower():
            for m in reversed(messages[:-1]):
                r, _ = _format_message(m)
                if "tool" in r.lower() or "functionexecution" in r.lower():
                    latest_msgs.insert(0, m)
                else:
                    break
        prev_messages = messages[: len(messages) - len(latest_msgs)]

        shown_sys_ids = {id(sm) for sm, _ in shown_sys_msgs}
        hidden_messages = [m for m in prev_messages if id(m) not in shown_sys_ids]

        role_counts: dict[str, int] = {}
        for m in hidden_messages:
            r, _ = _format_message(m)
            role_counts[r] = role_counts.get(r, 0) + 1
        breakdown = ", ".join(f"{cnt} {r}" for r, cnt in role_counts.items())

        if shown_sys_msgs:
            if hidden_messages:
                lines.append("-" * 96)
                lines.append(f"Context: {total_messages} messages ({len(shown_sys_msgs)} system prompt shown, {len(hidden_messages)} previous hidden: {breakdown})")
            else:
                lines.append(f"Context: {total_messages} messages ({len(shown_sys_msgs)} system prompt shown, 0 previous hidden)")
        else:
            lines.append(f"Context: {total_messages} messages ({len(prev_messages)} previous hidden: {breakdown})")

        if tools:
            tool_names = []
            for t in tools:
                if isinstance(t, dict):
                    tool_names.append(t.get("name") or t.get("function", {}).get("name", "tool"))
                elif hasattr(t, "name"):
                    tool_names.append(str(t.name))
                elif hasattr(t, "schema") and isinstance(t.schema, dict):
                    tool_names.append(t.schema.get("name", "tool"))
                else:
                    tool_names.append(str(t))
            lines.append(f"Tools: {len(tools)} tools ({', '.join(tool_names[:10])}{'...' if len(tool_names) > 10 else ''})")

        for lm in latest_msgs:
            if id(lm) not in shown_sys_ids:
                role, text = _format_message(lm)
                lines.append(f"--- Latest Message [{role}] ---")
                lines.append(text)

    lines.append("=" * 96)
    logger.info("\n".join(lines))


def _log_response(
    client: Any,
    result: CreateResult,
    stream: bool = False,
) -> None:
    if not is_openai_logging_enabled():
        return

    model_name = getattr(client, "model", None) or getattr(client, "_model", None) or "openai"
    stream_tag = " (stream)" if stream else ""
    finish_reason = getattr(result, "finish_reason", "stop")

    meta_parts = [f"Model: {model_name}", f"Finish: {finish_reason}"]
    if hasattr(result, "usage") and result.usage:
        p_tokens = getattr(result.usage, "prompt_tokens", 0)
        c_tokens = getattr(result.usage, "completion_tokens", 0)
        meta_parts.append(f"Usage: prompt={p_tokens}, completion={c_tokens}")
    if getattr(result, "cached", False):
        meta_parts.append("Cached: True")

    lines: list[str] = [
        f"{'=' * 38} [OpenAI Response{stream_tag}] {'=' * 37}",
        " | ".join(meta_parts),
    ]

    thought = getattr(result, "thought", None)
    if thought:
        lines.append("--- Thought ---")
        lines.append(str(thought))

    content = getattr(result, "content", "")
    if isinstance(content, list):
        tool_call_strs: list[str] = []
        for item in content:
            if hasattr(item, "name") and hasattr(item, "arguments"):
                tool_call_strs.append(f"{item.name}({item.arguments})")
            else:
                tool_call_strs.append(str(item))
        lines.append("--- Tool Calls ---")
        lines.extend(f"- {tc}" for tc in tool_call_strs)
    else:
        lines.append("--- Content ---")
        lines.append(str(content))

    lines.append("=" * 96)
    logger.info("\n".join(lines))


def _log_stream_chunks_summary(
    client: Any,
    chunks: list[str],
) -> None:
    if not is_openai_logging_enabled():
        return

    model_name = getattr(client, "model", None) or getattr(client, "_model", None) or "openai"
    full_text = "".join(chunks)
    lines: list[str] = [
        f"{'=' * 38} [OpenAI Response (stream)] {'=' * 30}",
        f"Model: {model_name} | Chunks: {len(chunks)}",
        "--- Content ---",
        full_text,
        "=" * 96,
    ]
    logger.info("\n".join(lines))


class LoggingChatCompletionClient(ChatCompletionClient):
    """ChatCompletionClient wrapper that emits structured OpenAI request/response logs."""

    def __init__(self, inner: ChatCompletionClient) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    @property
    def model(self) -> str:
        return getattr(self._inner, "model", None) or getattr(self._inner, "_model", "openai")

    @property
    def model_info(self) -> ModelInfo:
        return self._inner.model_info

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._inner.capabilities

    def actual_usage(self) -> RequestUsage:
        return self._inner.actual_usage()

    def total_usage(self) -> RequestUsage:
        return self._inner.total_usage()

    def count_tokens(self, messages: Sequence[LLMMessage], tools: Sequence[Any] = []) -> int:
        return self._inner.count_tokens(messages=messages, tools=tools)

    def remaining_tokens(self, messages: Sequence[LLMMessage], tools: Sequence[Any] = []) -> int:
        return self._inner.remaining_tokens(messages=messages, tools=tools)

    async def close(self) -> None:
        await self._inner.close()

    async def create(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[Any] = [],
        json_output: bool | None = None,
        extra_create_args: dict[str, Any] = {},
        cancellation_token: Any | None = None,
    ) -> CreateResult:
        _log_request(self._inner, messages, tools, stream=False)
        try:
            result = await self._inner.create(
                messages=messages,
                tools=tools,
                json_output=json_output,
                extra_create_args=extra_create_args,
                cancellation_token=cancellation_token,
            )
            _log_response(self._inner, result, stream=False)
            return result
        except Exception as exc:
            if is_openai_logging_enabled():
                model_name = getattr(self._inner, "model", "openai")
                logger.info(f"{'=' * 38} [OpenAI Error] {'=' * 41}\nModel: {model_name} | Error: {exc}\n{'=' * 96}")
            raise

    async def create_stream(
        self,
        messages: Sequence[LLMMessage],
        tools: Sequence[Any] = [],
        json_output: bool | None = None,
        extra_create_args: dict[str, Any] = {},
        cancellation_token: Any | None = None,
    ) -> AsyncIterator[Any]:
        _log_request(self._inner, messages, tools, stream=True)
        accumulated_chunks: list[str] = []
        logged_result = False
        try:
            async for chunk in self._inner.create_stream(
                messages=messages,
                tools=tools,
                json_output=json_output,
                extra_create_args=extra_create_args,
                cancellation_token=cancellation_token,
            ):
                if isinstance(chunk, CreateResult):
                    _log_response(self._inner, chunk, stream=True)
                    logged_result = True
                elif isinstance(chunk, str):
                    accumulated_chunks.append(chunk)
                yield chunk

            if not logged_result and accumulated_chunks:
                _log_stream_chunks_summary(self._inner, accumulated_chunks)
        except Exception as exc:
            if is_openai_logging_enabled():
                model_name = getattr(self._inner, "model", "openai")
                logger.info(f"{'=' * 38} [OpenAI Error (stream)] {'=' * 32}\nModel: {model_name} | Error: {exc}\n{'=' * 96}")
            raise


def wrap_client_if_logging_enabled(client: Any) -> Any:
    """Wrap a ChatCompletionClient with logging if enabled by environment flag."""
    if not is_openai_logging_enabled():
        return client
    if isinstance(client, LoggingChatCompletionClient):
        return client
    if not isinstance(client, ChatCompletionClient):
        return client
    return LoggingChatCompletionClient(client)
