"""Base classes and security validation utilities for harness tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Coroutine

from libhippo.runner.project import ProjectManager, ProjectSecurityError
from libhippo.runner.sandbox import SandboxRunner


class ToolExecutionError(Exception):
    """Raised when a tool operation fails."""

    pass


class BaseToolSuite:
    """Base tool suite providing path canonicalization, security policy checks, and event hooks."""

    def __init__(
        self,
        workspace_root: Path,
        sandbox: SandboxRunner,
        project_manager: ProjectManager,
        event_callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.sandbox = sandbox
        self.project_manager = project_manager
        self.event_callback = event_callback
        self._interactive_responses: dict[str, Any] = {}

    def resolve_path(self, path: str | Path | None) -> Path:
        """Resolve and validate a path inside workspace root or scratch directory."""
        target = self.workspace_root / (path or "")
        return self.sandbox.validate_path(target)

    async def emit_event(self, event_data: dict[str, Any]) -> None:
        """Emit tool lifecycle event if callback registered."""
        if self.event_callback:
            await self.event_callback(event_data)

    async def check_approval_if_needed(self, action: str, details: dict[str, Any]) -> None:
        """Check if request_review mode or project policy requires user confirmation."""
        from libhippo.runner.config import ExecutionMode

        if self.project_manager.config.mode == ExecutionMode.REQUEST_REVIEW:
            req_id = f"req-{abs(hash(str(details))) % 100000}"
            await self.emit_event(
                {
                    "type": "approval_request",
                    "request_id": req_id,
                    "action": action,
                    "details": details,
                }
            )
