"""3-Zone Context Memory architecture with deterministic lazy compaction."""

from __future__ import annotations

import datetime
from typing import Any

import tiktoken

from libhippo.runner.types import ContextMessage


class ContextMemory:
    """Manages the 3-Zone context memory and KV-cache prefix optimization."""

    def __init__(self, model_name: str = "gpt-4o") -> None:
        self.model_name = model_name
        self.zone1_prefix: list[ContextMessage] = []
        self.zone2_history: list[ContextMessage] = []
        try:
            self._encoder = tiktoken.encoding_for_model(model_name)
        except Exception:
            self._encoder = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, text: str) -> int:
        """Deterministically count tokens in a string using tiktoken."""
        if not text:
            return 0
        try:
            return len(self._encoder.encode(text))
        except Exception:
            return max(1, len(text) // 4)

    def set_zone1_prefix(
        self,
        system_persona: str,
        repo_profile: str,
        tool_definitions: list[dict[str, Any]] | None = None,
    ) -> None:
        """Initialize Zone 1 static prefix (bitwise invariant across turns)."""
        sections = [
            f"# Agent Role & System Instructions\n{system_persona.strip()}",
            f"# Repository Profile & Context\n{repo_profile.strip()}",
        ]
        if tool_definitions:
            tool_text = "\n".join(f"- {t.get('name')}: {t.get('description')}" for t in tool_definitions)
            sections.append(f"# Available Tool Suite\n{tool_text}")

        prefix_content = "\n\n---\n\n".join(sections)
        msg = ContextMessage(
            role="system",
            content=prefix_content,
            zone="zone1_prefix",
            raw_token_count=self.count_tokens(prefix_content),
            is_evictable=False,
        )
        self.zone1_prefix = [msg]

    def append_user_turn(
        self,
        user_content: str,
        timestamp: str | None = None,
        session_elapsed: str | None = None,
        branch: str | None = None,
    ) -> ContextMessage:
        """Append user turn prefixed with temporal metadata without busting Zone 1 cache."""
        ts = timestamp or datetime.datetime.now(datetime.timezone.utc).isoformat()
        elapsed = session_elapsed or "0s"
        br = branch or "main"

        meta_tag = f'<turn_metadata timestamp="{ts}" session_elapsed="{elapsed}" branch="{br}"/>'
        full_content = f"{meta_tag}\n{user_content}"
        msg = ContextMessage(
            role="user",
            content=full_content,
            zone="zone2_linear",
            raw_token_count=self.count_tokens(full_content),
            is_evictable=False,
            metadata={"timestamp": ts, "elapsed": elapsed, "branch": br},
        )
        self.zone2_history.append(msg)
        return msg

    def append_assistant_turn(self, content: str) -> ContextMessage:
        """Append model assistant response (reasoning trace and text)."""
        msg = ContextMessage(
            role="assistant",
            content=content,
            zone="zone2_linear",
            raw_token_count=self.count_tokens(content),
            is_evictable=False,
        )
        self.zone2_history.append(msg)
        return msg

    def append_tool_output(
        self,
        tool_name: str,
        content: str,
        tool_call_id: str | None = None,
        file_path_reference: str | None = None,
        is_evictable: bool = True,
    ) -> ContextMessage:
        """Append tool output into Zone 2 with evictability flag for Zone 3 compaction."""
        msg = ContextMessage(
            role="tool",
            content=content,
            zone="zone2_linear",
            tool_call_id=tool_call_id,
            file_path_reference=file_path_reference or tool_name,
            raw_token_count=self.count_tokens(content),
            is_evictable=is_evictable,
            metadata={"tool_name": tool_name},
        )
        self.zone2_history.append(msg)
        return msg

    def get_total_tokens(self) -> int:
        """Calculate total tokens currently active across Zone 1 and Zone 2."""
        z1 = sum(m.raw_token_count for m in self.zone1_prefix)
        z2 = sum(m.raw_token_count for m in self.zone2_history)
        return z1 + z2

    def compact_zone3(self, target_tokens: int) -> int:
        """Execute deterministic snippet compaction on historical tool outputs."""
        current_tokens = self.get_total_tokens()
        if current_tokens <= target_tokens:
            return 0

        tokens_evicted = 0
        # Scan Zone 2 from oldest to newest evictable tool outputs
        for msg in self.zone2_history:
            if not msg.is_evictable or msg.zone == "zone3_compacted":
                continue

            ref_pointer = f"[Referenced: {msg.file_path_reference or 'tool_output'}]"
            new_tokens = self.count_tokens(ref_pointer)
            delta = msg.raw_token_count - new_tokens

            if delta > 0:
                msg.content = ref_pointer
                msg.raw_token_count = new_tokens
                msg.zone = "zone3_compacted"
                tokens_evicted += delta
                current_tokens -= delta

            if current_tokens <= target_tokens:
                break

        return tokens_evicted

    def get_all_messages(self) -> list[ContextMessage]:
        """Return composite message sequence (Zone 1 prefix + Zone 2 history)."""
        return list(self.zone1_prefix) + list(self.zone2_history)

    def clear(self) -> None:
        """Reset memory by clearing Zone 1 prefix and Zone 2 history."""
        self.zone1_prefix.clear()
        self.zone2_history.clear()
