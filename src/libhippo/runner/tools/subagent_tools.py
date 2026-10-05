"""Subagent delegation, lifecycle management, and inter-agent messaging tools."""

from __future__ import annotations

from typing import Any

from libhippo.runner.tools.base import BaseToolSuite, ToolExecutionError
from libhippo.runner.types import ToolDefinition


class SubagentTools(BaseToolSuite):
    """Subagent orchestration tools supporting context inheritance and isolated worker tasks."""

    def __init__(self, *args: Any, subagent_manager: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.subagent_manager = subagent_manager

    async def invoke_subagent(
        self,
        type: str,
        prompt: str,
        role: str,
        context_mode: str = "inherit",
        model: str | None = None,
    ) -> dict[str, Any]:
        """Spawn child agent with inherit or isolated context mode."""
        if not self.subagent_manager:
            raise ToolExecutionError("Subagent manager is not initialized in this harness.")

        await self.check_approval_if_needed(
            "invoke_subagent",
            {"type": type, "role": role, "context_mode": context_mode},
        )

        sub_id = await self.subagent_manager.spawn(
            subagent_type=type,
            prompt=prompt,
            role=role,
            context_mode=context_mode,
            model=model,
        )
        return {
            "status": "spawned",
            "subagent_id": sub_id,
            "role": role,
            "context_mode": context_mode,
        }

    async def manage_subagents(
        self,
        action: str,
        subagent_id: str | None = None,
    ) -> dict[str, Any]:
        """Query status or cancel running subagents."""
        if not self.subagent_manager:
            raise ToolExecutionError("Subagent manager is not initialized.")

        if action == "list":
            return {"subagents": self.subagent_manager.list_active()}
        elif action == "kill" and subagent_id:
            await self.subagent_manager.kill(subagent_id)
            return {"status": "killed", "subagent_id": subagent_id}
        elif action == "status" and subagent_id:
            return self.subagent_manager.get_status(subagent_id)
        raise ToolExecutionError(f"Unsupported subagent action: {action}")

    async def send_message(self, recipient: str, message: str) -> dict[str, Any]:
        """Send message between harness and active subagent."""
        if not self.subagent_manager:
            raise ToolExecutionError("Subagent manager is not initialized.")
        res = await self.subagent_manager.send_message(recipient, message)
        return {"status": "sent", "recipient": recipient, "response": res}

    async def shorten_tool_output(
        self,
        tool_name: str | None = None,
        run_id: str | None = None,
        summary: str | None = None,
    ) -> dict[str, Any]:
        """Prune or discard a recent bulky tool output from context memory after inspecting it.

        Directly replaces the verbose output (e.g. large file reads, dense search results,
        compiler traces, test logs) with a concise summary in memory without extra roundtrips.
        """
        mem = getattr(self, "memory", None) or (
            getattr(self.subagent_manager, "memory", None) if self.subagent_manager else None
        )
        if not mem:
            raise ToolExecutionError("Context memory is not available for output shortening.")

        # Sanity check: search strictly within recent window of Zone 2 history
        # to ensure we never prune outputs from distant past turns.
        recency_window = 10
        recent_messages = mem.zone2_history[-recency_window:] if len(mem.zone2_history) > recency_window else mem.zone2_history
        target_msg = None

        for msg in reversed(recent_messages):
            if msg.role == "tool":
                matches_run_id = (run_id is None) or (msg.tool_call_id == run_id) or (msg.metadata.get("tool_call_id") == run_id)
                matches_tool = (tool_name is None) or (msg.metadata.get("tool_name") == tool_name)
                if matches_run_id and matches_tool:
                    target_msg = msg
                    break

        if not target_msg:
            # Check if it was in older history to provide a clear error message
            older_match = (
                any(
                    m.role == "tool"
                    and (
                        (run_id and (m.tool_call_id == run_id or m.metadata.get("tool_call_id") == run_id))
                        or (tool_name and m.metadata.get("tool_name") == tool_name)
                    )
                    for m in mem.zone2_history[:-recency_window]
                )
                if len(mem.zone2_history) > recency_window
                else False
            )

            if older_match:
                raise ToolExecutionError(
                    f"Refusing to shorten tool output: matching execution for tool_name='{tool_name}' / run_id='{run_id}' "
                    "occurred too far in the past."
                )

            detail = f"tool_name='{tool_name}'" if tool_name else ""
            if run_id:
                detail = f"{detail}, run_id='{run_id}'" if detail else f"run_id='{run_id}'"
            raise ToolExecutionError(f"No recent tool output found to shorten ({detail or 'any tool'}).")

        actual_tool = target_msg.metadata.get("tool_name") or tool_name or "tool"
        actual_run_id = target_msg.tool_call_id or run_id or ""

        old_tokens = target_msg.raw_token_count
        if summary:
            compact_text = f"[Tool output of {actual_tool} discarded/shortened by agent: {summary}]"
        else:
            compact_text = f"[Tool output of {actual_tool} discarded by agent]"

        old_lines = target_msg.content.count("\n") + 1
        target_msg.content = compact_text
        target_msg.raw_token_count = mem.count_tokens(compact_text)
        reclaimed_tokens = max(0, old_tokens - target_msg.raw_token_count)

        return {
            "status": "shortened",
            "tool_name": actual_tool,
            "run_id": actual_run_id,
            "summary": summary,
            "result": compact_text,
            "old_lines": old_lines,
            "reclaimed_tokens": reclaimed_tokens,
        }


    def get_tool_definitions(self) -> dict[str, ToolDefinition]:
        """Return ToolDefinition schemas for subagents."""
        return {
            "invoke_subagent": ToolDefinition(
                name="invoke_subagent",
                description="Spawn child subagent with inherit or isolated context mode.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "description": "Type name of subagent"},
                        "prompt": {"type": "string", "description": "Actionable task directive"},
                        "role": {"type": "string", "description": "Role/job title of subagent"},
                        "context_mode": {"type": "string", "enum": ["inherit", "isolated"], "default": "inherit"},
                        "model": {"type": "string", "description": "Optional model override"},
                    },
                    "required": ["type", "prompt", "role"],
                },
                handler=self.invoke_subagent,
            ),
            "manage_subagents": ToolDefinition(
                name="manage_subagents",
                description="Manage spawned subagents (list, status, kill).",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "status", "kill"]},
                        "subagent_id": {"type": "string"},
                    },
                    "required": ["action"],
                },
                handler=self.manage_subagents,
            ),
            "send_message": ToolDefinition(
                name="send_message",
                description="Send message to a subagent conversation.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "recipient": {"type": "string", "description": "Subagent conversation ID"},
                        "message": {"type": "string", "description": "Message content"},
                    },
                    "required": ["recipient", "message"],
                },
                handler=self.send_message,
            ),
            "shorten_tool_output": ToolDefinition(
                name="shorten_tool_output",
                description=(
                    "Prune or discard a recent bulky tool output (e.g. large file reads, dense search results, "
                    "huge directory trees, compiler traces, or test logs) from context memory after inspecting it. "
                    "Directly compacts the output in memory with zero extra roundtrips. "
                    "For delegating problems to subagents, use invoke_subagent instead."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "tool_name": {
                            "type": "string",
                            "description": "Name of the recent tool whose output to prune (e.g. 'read_file', 'search_file', 'run_command', 'list_dir')",
                        },
                        "run_id": {
                            "type": "string",
                            "description": "Optional tool_call_id / run ID for sanity checking the specific tool execution",
                        },
                        "summary": {
                            "type": "string",
                            "description": "Optional concise note of what was diagnosed or why the raw output was pruned",
                        },
                    },
                },
                handler=self.shorten_tool_output,
            ),
        }
