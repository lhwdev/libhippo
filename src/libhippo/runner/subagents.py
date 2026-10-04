"""Subagent lifecycle manager supporting inherit and isolated context modes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from autogen_core.models import ChatCompletionClient

from libhippo.runner.memory import ContextMemory
from libhippo.runner.persistence import ConversationSession


@dataclass
class SubagentInstance:
    """Tracks state and memory for a child delegate worker."""

    subagent_id: str
    role: str
    prompt: str
    context_mode: Literal["inherit", "isolated"]
    status: Literal["running", "idle", "completed", "errored"] = "idle"
    result: str = ""
    error: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)


class SubagentManager:
    """Orchestrates child subagent lifecycles and concurrency."""

    def __init__(
        self,
        model_client: ChatCompletionClient,
        session: ConversationSession | None = None,
    ) -> None:
        self.model_client = model_client
        self.session = session
        self.subagents: dict[str, SubagentInstance] = {}

    async def invoke_subagent(
        self,
        role: str,
        prompt: str,
        context_mode: Literal["inherit", "isolated"] = "inherit",
        parent_memory: ContextMemory | None = None,
    ) -> SubagentInstance:
        """Spawn a child worker with inherited or isolated context."""
        sub_id = f"sub-{uuid.uuid4().hex[:8]}"

        initial_messages: list[dict[str, Any]] = []
        if context_mode == "inherit" and parent_memory:
            # Reuses parent Zone 1 static prefix + Zone 2 history for warm KV-cache hit
            for m in parent_memory.get_all_messages():
                initial_messages.append({"role": m.role, "content": m.content})
        else:
            # Clean-slate sub-1k context
            initial_messages.append({"role": "system", "content": f"You are a specialized subagent performing: {role}."})

        initial_messages.append({"role": "user", "content": prompt})

        instance = SubagentInstance(
            subagent_id=sub_id,
            role=role,
            prompt=prompt,
            context_mode=context_mode,
            status="running",
            messages=initial_messages,
        )
        self.subagents[sub_id] = instance

        if self.session:
            await self.session.record_subagent(sub_id, {
                "subagent_id": sub_id,
                "role": role,
                "status": "running",
                "context_mode": context_mode,
            })

        return instance

    async def send_message(self, subagent_id: str, message: str) -> None:
        """Send inter-agent message to child worker."""
        if subagent_id not in self.subagents:
            raise KeyError(f"Subagent '{subagent_id}' not found.")
        instance = self.subagents[subagent_id]
        instance.messages.append({"role": "user", "content": message})

    def get_status(self, subagent_id: str) -> dict[str, Any]:
        """Query subagent status."""
        if subagent_id not in self.subagents:
            return {"status": "error", "error": f"Subagent '{subagent_id}' not found."}
        inst = self.subagents[subagent_id]
        return {
            "subagent_id": inst.subagent_id,
            "role": inst.role,
            "status": inst.status,
            "result": inst.result,
        }

    async def spawn(
        self,
        subagent_type: str,
        prompt: str,
        role: str,
        context_mode: Literal["inherit", "isolated"] = "inherit",
        model: str | None = None,
        parent_memory: ContextMemory | None = None,
    ) -> str:
        """Spawn a child worker and return subagent_id."""
        mem = parent_memory or getattr(self, "memory", None)
        inst = await self.invoke_subagent(
            role=role,
            prompt=prompt,
            context_mode=context_mode,
            parent_memory=mem,
        )
        return inst.subagent_id

    def list_active(self) -> list[dict[str, Any]]:
        """List active subagents."""
        return [
            {
                "subagent_id": s.subagent_id,
                "role": s.role,
                "status": s.status,
                "context_mode": s.context_mode,
            }
            for s in self.subagents.values()
        ]

    async def kill(self, subagent_id: str) -> None:
        """Terminate a subagent."""
        if subagent_id in self.subagents:
            self.subagents[subagent_id].status = "errored"
            self.subagents[subagent_id].error = "Terminated by parent"

    async def execute_task(
        self,
        instruction: str,
        role: str = "Delegate Task Worker",
        parent_memory: ContextMemory | None = None,
    ) -> str:
        """Execute a delegated subagent task against warm parent KV cache."""
        mem = parent_memory or getattr(self, "memory", None)
        inst = await self.invoke_subagent(
            role=role,
            prompt=instruction,
            context_mode="inherit",
            parent_memory=mem,
        )

        from autogen_core.models import AssistantMessage, SystemMessage, UserMessage

        llm_msgs = []
        for m in inst.messages:
            if m["role"] == "system":
                llm_msgs.append(SystemMessage(content=m["content"]))
            elif m["role"] == "assistant":
                llm_msgs.append(AssistantMessage(content=m["content"], source="assistant"))
            else:
                llm_msgs.append(UserMessage(content=m["content"], source="user"))

        try:
            res = await self.model_client.create(messages=llm_msgs)
            res_text = res.content if isinstance(res.content, str) else str(res.content)
            await self.complete_subagent(inst.subagent_id, res_text)
            return res_text
        except Exception as e:
            inst.status = "errored"
            inst.error = str(e)
            return f"[Subagent execution error: {e}]"

    async def complete_subagent(self, subagent_id: str, result: str) -> None:
        """Mark subagent finished with result summary."""
        if subagent_id in self.subagents:
            inst = self.subagents[subagent_id]
            inst.status = "completed"
            inst.result = result
            if self.session:
                await self.session.record_subagent(subagent_id, {
                    "subagent_id": subagent_id,
                    "role": inst.role,
                    "status": "completed",
                    "result": result,
                })
