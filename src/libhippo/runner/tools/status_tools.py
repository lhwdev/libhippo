"""System status and developer interaction tools."""

from __future__ import annotations

import datetime
import getpass
import platform
import subprocess
import time
from typing import Any

from libhippo.runner.tools.base import BaseToolSuite
from libhippo.runner.types import ToolDefinition


class StatusTools(BaseToolSuite):
    """Ambient environment reporting and interactive user clarification tools."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._start_monotonic = time.monotonic()

    async def get_status(self) -> dict[str, Any]:
        """Programmatically retrieve current time, active git branch, user info, and session duration."""
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        elapsed_sec = int(time.monotonic() - self._start_monotonic)

        # Get active git branch if available
        branch = "unknown"
        try:
            res = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                branch = res.stdout.strip()
        except Exception:
            pass

        return {
            "current_time_utc": now_utc.isoformat(),
            "local_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "session_elapsed_seconds": elapsed_sec,
            "session_elapsed_formatted": f"{elapsed_sec // 60}m {elapsed_sec % 60}s",
            "active_git_branch": branch,
            "user": getpass.getuser(),
            "platform": platform.platform(),
            "workspace": str(self.workspace_root),
        }

    async def ask_question(self, questions: list[dict[str, Any]]) -> dict[str, Any]:
        """Render interactive question modal to clarify requirements or resolve ambiguous trade-offs."""
        q_id = f"q-{abs(hash(str(questions))) % 100000}"
        await self.emit_event(
            {
                "type": "modal_question",
                "question_id": q_id,
                "questions": questions,
            }
        )

        if q_id in self._interactive_responses:
            return self._interactive_responses[q_id]

        return {
            "status": "answered",
            "answers": [f"Selected option 1 for: {q.get('question')}" for q in questions],
        }

    def get_tool_definitions(self) -> dict[str, ToolDefinition]:
        """Return ToolDefinition schemas for status and interaction."""
        return {
            "get_status": ToolDefinition(
                name="get_status",
                description="Get current system time, timezone, git branch, and session elapsed time.",
                parameters_schema={"type": "object", "properties": {}},
                handler=self.get_status,
            ),
            "ask_question": ToolDefinition(
                name="ask_question",
                description="Prompt the user with an interactive question modal.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "question": {"type": "string"},
                                    "options": {"type": "array", "items": {"type": "string"}},
                                    "is_multi_select": {"type": "boolean"},
                                },
                                "required": ["question", "options"],
                            },
                        },
                    },
                    "required": ["questions"],
                },
                handler=self.ask_question,
            ),
        }
