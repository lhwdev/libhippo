"""Terminal execution and background task management tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from libhippo.runner.project import ProjectSecurityError
from libhippo.runner.tools.base import BaseToolSuite, ToolExecutionError
from libhippo.runner.triage import OutputTriage
from libhippo.runner.types import ToolDefinition


class TerminalTools(BaseToolSuite):
    """Execution tools managing sandboxed shell commands and long-running background tasks."""

    async def run_command(
        self,
        command_line: str,
        cwd: str | None = None,
        wait_ms: int = 2000,
        bypass_sandbox: bool = False,
        is_daemon: bool = False,
    ) -> dict[str, Any]:
        """Execute bash command inside the Bubblewrap sandbox or host fallback."""
        target_cwd = self.resolve_path(cwd)
        decision = self.project_manager.check_command(command_line)
        if decision == "deny":
            raise ProjectSecurityError(f"Command execution denied by security policy: '{command_line}'")

        if bypass_sandbox and not self.project_manager.config.allow_sandbox_bypass:
            await self.check_approval_if_needed("bypass_sandbox", {"command": command_line})

        await self.check_approval_if_needed("run_command", {"command": command_line, "cwd": str(target_cwd)})

        result = await self.sandbox.run_command(
            command_line=command_line,
            cwd=target_cwd,
            wait_ms=wait_ms,
            bypass_sandbox=bypass_sandbox,
            is_daemon=is_daemon,
        )

        # Apply output triage if command returned synchronously
        if result.get("status") == "completed":
            raw_out = result.get("output", "")
            triage = OutputTriage.classify_output(command_line, raw_out)
            result["triage"] = triage
            result["output"] = triage["summary"]

        return result

    async def manage_task(
        self,
        action: str,
        task_id: str | None = None,
        input: str | None = None,
    ) -> dict[str, Any]:
        """Manage background process tasks (status, list, kill, send_input, wait)."""
        await self.check_approval_if_needed("manage_task", {"action": action, "task_id": task_id})
        return await self.sandbox.manage_task(action=action, task_id=task_id, input=input)

    def get_tool_definitions(self) -> dict[str, ToolDefinition]:
        """Return ToolDefinition schemas for terminal and task execution."""
        return {
            "run_command": ToolDefinition(
                name="run_command",
                description="Run shell commands inside rootless sandbox container.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "command_line": {"type": "string", "description": "Shell command to run"},
                        "cwd": {"type": "string", "description": "Working directory relative to workspace root"},
                        "wait_ms": {"type": "integer", "description": "Wait ceiling before detaching to background"},
                        "bypass_sandbox": {"type": "boolean", "description": "Run unsandboxed on host"},
                        "is_daemon": {"type": "boolean", "description": "Process should keep running indefinitely"},
                    },
                    "required": ["command_line"],
                },
                handler=self.run_command,
            ),
            "manage_task": ToolDefinition(
                name="manage_task",
                description="Manage background tasks (list, status, wait, kill, send_input).",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "status", "wait", "kill", "send_input"]},
                        "task_id": {"type": "string", "description": "ID of background task to manage"},
                        "input": {"type": "string", "description": "Input string to send to stdin"},
                    },
                    "required": ["action"],
                },
                handler=self.manage_task,
            ),
        }
