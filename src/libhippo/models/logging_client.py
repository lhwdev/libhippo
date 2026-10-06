"""Transparent logging wrapper for ChatCompletionClient instances."""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import sys
from typing import Any, AsyncIterator, Sequence

from rich.console import Console
from rich.text import Text

from autogen_core.models import (
    ChatCompletionClient,
    CreateResult,
    LLMMessage,
    ModelCapabilities,
    ModelInfo,
    RequestUsage,
)

_env_loaded = False
logger = logging.getLogger("libhippo.openai")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
logger.propagate = False

_console = Console(file=io.StringIO(), force_terminal=True, color_system="standard", width=1000, soft_wrap=True)


def _render_ansi(text_obj: Text) -> str:
    """Render a rich Text object to an ANSI-escaped string."""
    buf = io.StringIO()
    c = Console(file=buf, force_terminal=True, color_system="standard", width=1000, soft_wrap=True)
    c.print(text_obj)
    return buf.getvalue().rstrip("\n")


class _StripAnsiFilter(logging.Filter):
    _ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._ANSI_RE.sub("", record.msg)
        return True


_file_handler_initialized = False


def _setup_file_handler() -> None:
    global _file_handler_initialized
    if _file_handler_initialized:
        return
    log_file = os.getenv("LIBHIPPO_LOG_FILE", "logs/libhippo.log")
    try:
        abs_path = os.path.abspath(log_file)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        for h in logger.handlers:
            if isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None) == abs_path:
                _file_handler_initialized = True
                return
        fh = logging.FileHandler(abs_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        fh.addFilter(_StripAnsiFilter())
        logger.addHandler(fh)
        _file_handler_initialized = True
    except Exception:
        pass


def is_openai_logging_enabled() -> bool:
    """Check if OpenAI request/response logging is enabled via environment."""
    global _env_loaded
    if not _env_loaded:
        try:
            from libhippo.runner.env import load_env_hierarchy

            load_env_hierarchy()
        except ImportError:
            pass
        _env_loaded = True
        _setup_file_handler()

    val = (
        os.getenv("LIBHIPPO_LOG_OPENAI")
        or os.getenv("OPENAI_LOG")
        or os.getenv("LOG_OPENAI")
        or ""
    ).strip().lower()
    return val in ("1", "true", "yes", "on", "enable", "enabled")


def _truncate_text(text: str, max_chars: int = 1000, max_lines: int = 30) -> str:
    lines = text.splitlines(keepends=True)
    if len(lines) > max_lines:
        truncated_by_lines = "".join(lines[:max_lines])
        truncated_lines_count = len(lines) - max_lines
    else:
        truncated_by_lines = None
        truncated_lines_count = 0

    if len(text) > max_chars:
        truncated_by_chars = text[:max_chars]
        truncated_chars_count = len(text) - max_chars
    else:
        truncated_by_chars = None
        truncated_chars_count = 0

    if truncated_by_lines is None and truncated_by_chars is None:
        return text

    if truncated_by_lines is not None and truncated_by_chars is not None:
        if len(truncated_by_lines) <= len(truncated_by_chars):
            return truncated_by_lines.rstrip("\r\n") + f"\n... [truncated {truncated_lines_count} lines] ..."
        else:
            return truncated_by_chars + f"\n... [truncated {truncated_chars_count} characters] ..."
    elif truncated_by_lines is not None:
        return truncated_by_lines.rstrip("\r\n") + f"\n... [truncated {truncated_lines_count} lines] ..."
    elif truncated_by_chars is not None:
        return truncated_by_chars + f"\n... [truncated {truncated_chars_count} characters] ..."
    else:
        return text


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
                parts.append(f"[{part_name}:{part.call_id}] {_truncate_text(str(part.content))}")
            elif hasattr(part, "content"):
                parts.append(str(part.content))
            else:
                parts.append(str(part))
        text = "\n".join(parts)
    else:
        text = str(content)
        if "tool" in role.lower() or "functionexecution" in role.lower():
            text = _truncate_text(text)
    return role, text


def _log_request(
    client: Any,
    messages: Sequence[LLMMessage] | Sequence[Any],
    tools: Sequence[Any] = [],
    stream: bool = False,
) -> None:
    if not is_openai_logging_enabled():
        return

    model_name = str(getattr(client, "model", None) or getattr(client, "_model", None) or "openai")
    agent_role = str(getattr(client, "agent_role", None)) if getattr(client, "agent_role", None) else None
    agent_tag = f" [Agent: {agent_role}]" if agent_role else ""
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
    banner_text = Text("=" * 38, style="dim cyan")
    banner_text.append(f" [OpenAI Request{stream_tag}] ", style="bold not dim cyan")
    banner_text.append("=" * (40 if not stream else 31), style="dim cyan")
    lines: list[str] = [
        _render_ansi(banner_text),
    ]

    model_text = Text("Model: ", style="dim")
    model_text.append(model_name, style="bold not dim cyan")
    if agent_role:
        model_text.append(" [Agent: ", style="dim")
        model_text.append(agent_role, style="bold not dim magenta")
        model_text.append("]", style="dim")
    lines.append(_render_ansi(model_text))

    # Show system prompt(s) on first send
    for _, text in shown_sys_msgs:
        lines.append(_render_ansi(Text("--- System Prompt (First Send) ---", style="bold yellow")))
        lines.append(text)

    if total_messages == 0:
        lines.append(_render_ansi(Text("Context: 0 messages", style="dim")))
    elif total_messages == 1:
        if not shown_sys_msgs:
            role, text = _format_message(messages[0])
            ctx_t = Text("Context: ", style="dim")
            ctx_t.append("1 message (0 previous hidden)", style="dim")
            lines.append(_render_ansi(ctx_t))
            role_t = Text("--- Latest Message [", style="bold yellow")
            role_t.append(role, style="bold not dim cyan")
            role_t.append("] ---", style="bold yellow")
            lines.append(_render_ansi(role_t))
            lines.append(text)
        else:
            lines.append(_render_ansi(Text("Context: 1 message (system prompt shown above)", style="dim")))
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
                lines.append(_render_ansi(Text("-" * 96, style="dim")))
                ctx_t = Text("Context: ", style="dim")
                ctx_t.append(f"{total_messages} messages ", style="bold not dim white")
                ctx_t.append(f"({len(shown_sys_msgs)} system prompt shown, {len(hidden_messages)} previous hidden: {breakdown})", style="dim")
                lines.append(_render_ansi(ctx_t))
            else:
                ctx_t = Text("Context: ", style="dim")
                ctx_t.append(f"{total_messages} messages ", style="bold not dim white")
                ctx_t.append(f"({len(shown_sys_msgs)} system prompt shown, 0 previous hidden)", style="dim")
                lines.append(_render_ansi(ctx_t))
        else:
            ctx_t = Text("Context: ", style="dim")
            ctx_t.append(f"{total_messages} messages ", style="bold not dim white")
            ctx_t.append(f"({len(prev_messages)} previous hidden: {breakdown})", style="dim")
            lines.append(_render_ansi(ctx_t))

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
            tools_t = Text("Tools: ", style="dim")
            tools_t.append(f"{len(tools)} tools ", style="bold not dim green")
            tools_t.append(f"({', '.join(tool_names[:10])}{'...' if len(tool_names) > 10 else ''})", style="dim")
            lines.append(_render_ansi(tools_t))

        for lm in latest_msgs:
            if id(lm) not in shown_sys_ids:
                role, text = _format_message(lm)
                role_t = Text("--- Latest Message [", style="bold yellow")
                role_t.append(role, style="bold not dim cyan")
                role_t.append("] ---", style="bold yellow")
                lines.append(_render_ansi(role_t))
                lines.append(text)

    lines.append(_render_ansi(Text("=" * 96, style="dim cyan")))
    logger.info("\n".join(lines))


def _log_response(
    client: Any,
    result: CreateResult,
    stream: bool = False,
) -> None:
    if not is_openai_logging_enabled():
        return

    model_name = str(getattr(client, "model", None) or getattr(client, "_model", None) or "openai")
    agent_role = str(getattr(client, "agent_role", None)) if getattr(client, "agent_role", None) else None
    stream_tag = " (stream)" if stream else ""
    finish_reason = getattr(result, "finish_reason", "stop")

    p_tokens = 0
    c_tokens = 0
    if hasattr(result, "usage") and result.usage:
        p_tokens = getattr(result.usage, "prompt_tokens", 0) or 0
        c_tokens = getattr(result.usage, "completion_tokens", 0) or 0

    cached_tokens = int(
        getattr(result, "cached_tokens", None)
        or (getattr(result.usage, "cached_tokens", None) if hasattr(result, "usage") and result.usage else None)
        or 0
    )
    cache_write_tokens = int(
        getattr(result, "cache_write_tokens", None)
        or (getattr(result.usage, "cache_write_tokens", None) if hasattr(result, "usage") and result.usage else None)
        or 0
    )
    reasoning_tokens = int(
        getattr(result, "reasoning_tokens", None)
        or (getattr(result.usage, "reasoning_tokens", None) if hasattr(result, "usage") and result.usage else None)
        or 0
    )

    resp_banner = Text("=" * 38, style="dim green")
    resp_banner.append(f" [OpenAI Response{stream_tag}] ", style="bold not dim green")
    resp_banner.append("=" * (39 if not stream else 30), style="dim green")
    lines: list[str] = [
        _render_ansi(resp_banner),
    ]

    meta_t = Text("Model: ", style="dim")
    meta_t.append(model_name, style="bold not dim cyan")
    if agent_role:
        meta_t.append(" [Agent: ", style="dim")
        meta_t.append(agent_role, style="bold not dim magenta")
        meta_t.append("]", style="dim")
    meta_t.append(" | Finish: ", style="dim")
    meta_t.append(str(finish_reason), style="bold not dim yellow" if finish_reason != "stop" else "dim")

    if p_tokens or c_tokens:
        meta_t.append(" | Usage: ", style="dim")
        meta_t.append(f"prompt={p_tokens}, completion={c_tokens}", style="not dim")

    if cached_tokens > 0:
        pct = (cached_tokens / p_tokens * 100) if p_tokens > 0 else 100.0
        meta_t.append(" | Cache: ", style="dim")
        meta_t.append(f"HIT ({cached_tokens}/{p_tokens}, {pct:.1f}%)", style="bold not dim green")
    elif cache_write_tokens > 0:
        meta_t.append(" | Cache: ", style="dim")
        meta_t.append(f"WRITE ({cache_write_tokens} tokens)", style="bold not dim blue")
    elif getattr(result, "cached", False):
        meta_t.append(" | Cache: ", style="dim")
        meta_t.append("HIT", style="bold not dim green")
    else:
        meta_t.append(" | Cache: ", style="dim")
        meta_t.append("MISS", style="dim")

    if reasoning_tokens > 0:
        meta_t.append(" | Reasoning: ", style="dim")
        meta_t.append(f"{reasoning_tokens} tokens", style="bold not dim cyan")

    lines.append(_render_ansi(meta_t))

    thought = getattr(result, "thought", None)
    if thought:
        lines.append(_render_ansi(Text("--- Thought ---", style="italic dim cyan")))
        lines.append(str(thought))

    content = getattr(result, "content", "")
    if isinstance(content, list):
        tool_call_strs: list[str] = []
        for item in content:
            if hasattr(item, "name") and hasattr(item, "arguments"):
                tool_call_strs.append(f"{item.name}({item.arguments})")
            else:
                tool_call_strs.append(str(item))
        lines.append(_render_ansi(Text("--- Tool Calls ---", style="bold cyan")))
        for tc in tool_call_strs:
            tc_t = Text("- ", style="dim")
            tc_t.append(tc, style="bold not dim yellow")
            lines.append(_render_ansi(tc_t))
    else:
        lines.append(_render_ansi(Text("--- Content ---", style="bold green")))
        lines.append(str(content))

    lines.append(_render_ansi(Text("=" * 96, style="dim green")))
    logger.info("\n".join(lines))


def _log_stream_chunks_summary(
    client: Any,
    chunks: list[str],
) -> None:
    if not is_openai_logging_enabled():
        return

    model_name = str(getattr(client, "model", None) or getattr(client, "_model", None) or "openai")
    agent_role = str(getattr(client, "agent_role", None)) if getattr(client, "agent_role", None) else None
    agent_tag = f" [Agent: {agent_role}]" if agent_role else ""
    full_text = "".join(chunks)

    banner_t = Text("=" * 38, style="dim green")
    banner_t.append(" [OpenAI Response (stream)] ", style="bold not dim green")
    banner_t.append("=" * 30, style="dim green")
    lines: list[str] = [
        _render_ansi(banner_t),
    ]

    meta_t = Text("Model: ", style="dim")
    meta_t.append(model_name, style="bold not dim cyan")
    if agent_role:
        meta_t.append(" [Agent: ", style="dim")
        meta_t.append(agent_role, style="bold not dim magenta")
        meta_t.append("]", style="dim")
    meta_t.append(" | Chunks: ", style="dim")
    meta_t.append(str(len(chunks)), style="not dim")
    lines.append(_render_ansi(meta_t))

    lines.append(_render_ansi(Text("--- Content ---", style="bold green")))
    lines.append(full_text)
    lines.append(_render_ansi(Text("=" * 96, style="dim green")))
    logger.info("\n".join(lines))


class LoggingChatCompletionClient(ChatCompletionClient):
    """ChatCompletionClient wrapper that emits structured OpenAI request/response logs."""

    def __init__(self, inner: ChatCompletionClient, agent_role: str | None = None) -> None:
        self._inner = inner
        self.agent_role = agent_role

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
                err_banner = Text("=" * 38, style="dim red")
                err_banner.append(" [OpenAI Error] ", style="bold not dim red")
                err_banner.append("=" * 41, style="dim red")
                err_meta = Text("Model: ", style="dim")
                err_meta.append(model_name, style="bold not dim cyan")
                if self.agent_role:
                    err_meta.append(" [Agent: ", style="dim")
                    err_meta.append(self.agent_role, style="bold not dim magenta")
                    err_meta.append("]", style="dim")
                err_meta.append(" | Error: ", style="dim")
                err_meta.append(str(exc), style="bold not dim red")
                err_lines = [
                    _render_ansi(err_banner),
                    _render_ansi(err_meta),
                    _render_ansi(Text("=" * 96, style="dim red")),
                ]
                logger.info("\n".join(err_lines))
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
                err_banner = Text("=" * 38, style="dim red")
                err_banner.append(" [OpenAI Error (stream)] ", style="bold not dim red")
                err_banner.append("=" * 32, style="dim red")
                err_meta = Text("Model: ", style="dim")
                err_meta.append(model_name, style="bold not dim cyan")
                if self.agent_role:
                    err_meta.append(" [Agent: ", style="dim")
                    err_meta.append(self.agent_role, style="bold not dim magenta")
                    err_meta.append("]", style="dim")
                err_meta.append(" | Error: ", style="dim")
                err_meta.append(str(exc), style="bold not dim red")
                err_lines = [
                    _render_ansi(err_banner),
                    _render_ansi(err_meta),
                    _render_ansi(Text("=" * 96, style="dim red")),
                ]
                logger.info("\n".join(err_lines))
            raise


def wrap_client_if_logging_enabled(client: Any, agent_role: str | None = None) -> Any:
    """Wrap a ChatCompletionClient with logging if enabled by environment flag."""
    if not is_openai_logging_enabled():
        return client
    if isinstance(client, LoggingChatCompletionClient):
        if agent_role and not getattr(client, "agent_role", None):
            client.agent_role = agent_role
        return client
    if not isinstance(client, ChatCompletionClient):
        return client
    return LoggingChatCompletionClient(client, agent_role=agent_role)
