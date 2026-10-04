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
        instruction: str,
        tool_name: str | None = None,
    ) -> dict[str, Any]:
        """Fork a subagent with inherited context to carry out instruction on previous tool output and shorten it."""
        if not self.subagent_manager:
            raise ToolExecutionError("Subagent manager is not initialized.")

        mem = getattr(self, "memory", None) or getattr(self.subagent_manager, "memory", None)
        if not mem:
            raise ToolExecutionError("Context memory is not available for output shortening.")

        target_msg = mem.get_last_tool_output(tool_name)
        if not target_msg:
            raise ToolExecutionError(f"No previous tool output found to shorten (tool_name={tool_name}).")

        await self.check_approval_if_needed(
            "shorten_tool_output",
            {"instruction": instruction, "tool_name": tool_name or target_msg.metadata.get("tool_name")},
        )

        sub_result = await self.subagent_manager.execute_task(
            instruction=instruction,
            role=f"Output Delegator for {target_msg.metadata.get('tool_name', 'tool')}",
            parent_memory=mem,
        )

        compact_text = (
            f"[Tool output shortened via subagent]\n"
            f"Directive: {instruction}\n"
            f"Result:\n{sub_result}"
        )
        mem.replace_tool_output(
            new_content=compact_text,
            tool_name=target_msg.metadata.get("tool_name"),
            tool_call_id=target_msg.tool_call_id,
        )

        return {
            "status": "shortened",
            "instruction": instruction,
            "tool_name": target_msg.metadata.get("tool_name"),
            "result": sub_result,
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
                description="Fork a subagent to carry out an instruction on a large/verbose tool output (e.g. summarize, diagnose, or extract) and replace the bulky output in main context with the concise result.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "instruction": {
                            "type": "string",
                            "description": "Specific directive for the subagent (e.g. 'Summarize failing tests', 'Diagnose type error and fix')",
                        },
                        "tool_name": {
                            "type": "string",
                            "description": "Optional name of the tool output to shorten (defaults to the most recent tool output)",
                        },
                    },
                    "required": ["instruction"],
                },
                handler=self.shorten_tool_output,
            ),
        }
