"""Ephemeral sidecar agent executing concurrent mid-run /btw queries against warm KV cache."""

from __future__ import annotations

from typing import Any

from autogen_core.models import AssistantMessage, ChatCompletionClient, SystemMessage, UserMessage

from libhippo.runner.memory import ContextMemory


class SidecarExecutor:
    """Executes read-only Q&A queries without disturbing primary agent execution."""

    def __init__(self, model_client: ChatCompletionClient) -> None:
        self.model_client = model_client

    async def ask(self, query: str, parent_memory: ContextMemory) -> str:
        """Execute query using snapshot of warm parent KV-cache context."""
        # 1. Snapshot parent context up to current turn
        parent_messages = parent_memory.get_all_messages()

        llm_messages: list[Any] = []
        for m in parent_messages:
            if m.role == "system":
                llm_messages.append(SystemMessage(content=m.content))
            elif m.role in ("user", "tool"):
                llm_messages.append(UserMessage(content=m.content, source="user"))
            elif m.role == "assistant":
                llm_messages.append(AssistantMessage(content=m.content, source="assistant"))

        # 2. Append sidecar instruction and user question
        sidecar_prompt = (
            "[Sidecar /btw Inquiry]\n"
            "You are an ephemeral sidecar assistant. Provide a concise, direct answer "
            "to the user's question based strictly on the current context snapshot. "
            "Do not modify state or execute actions.\n\n"
            f"User Question: {query}"
        )
        llm_messages.append(UserMessage(content=sidecar_prompt, source="sidecar_user"))

        # 3. Call model (benefits from 100% KV-cache read hit on parent prefix)
        try:
            res = await self.model_client.create(messages=llm_messages)
            return res.content if isinstance(res.content, str) else str(res.content)
        except Exception as e:
            return f"Sidecar error: {e}"
