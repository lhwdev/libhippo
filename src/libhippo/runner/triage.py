"""Output-aware tool execution triage and subagent fan-out coordinator."""

from __future__ import annotations

import re
from typing import Any, Literal


class OutputTriage:
    """Classifies tool outputs to prevent context window saturation."""

    @staticmethod
    def classify_output(
        command_or_tool: str,
        output: str,
        max_short_lines: int = 30,
    ) -> dict[str, Any]:
        """Classify output into short, long-unimportant, or long-important."""
        lines = output.splitlines()
        line_count = len(lines)

        # 1. Short tier: fast-path directly into Zone 2
        if line_count <= max_short_lines:
            return {
                "tier": "short",
                "line_count": line_count,
                "summary": output,
                "subproblems": [],
            }

        # Check for actionable compiler / test error patterns
        error_indicators = [
            r"error:",
            r"failed:",
            r"FAILED tests/",
            r"SyntaxError:",
            r"TypeError:",
            r"ValueError:",
            r"TS\d{4}:",
            r"assert ",
        ]
        has_actionable_errors = any(re.search(pat, output, re.IGNORECASE) for pat in error_indicators)

        if has_actionable_errors:
            # 2. Long-important tier: decompose into subproblems
            subproblems = OutputTriage._extract_subproblems(lines)
            summary = (
                f"[Output Truncated: {line_count} lines]\n"
                f"Identified {len(subproblems)} distinct error clusters across command '{command_or_tool}'."
            )
            return {
                "tier": "long-important",
                "line_count": line_count,
                "summary": summary,
                "subproblems": subproblems,
            }
        else:
            # 3. Long-unimportant tier: high volume telemetry/logs
            first_two = "\n".join(lines[:2])
            last_two = "\n".join(lines[-2:]) if line_count > 2 else ""
            summary = (
                f"[Output Truncated: {line_count} lines of verbose log preserved in task log]\n"
                f"Header:\n{first_two}\n...\nTail:\n{last_two}"
            )
            return {
                "tier": "long-unimportant",
                "line_count": line_count,
                "summary": summary,
                "subproblems": [],
            }

    @staticmethod
    def _extract_subproblems(lines: list[str]) -> list[str]:
        """Group error lines into discrete subproblem clusters."""
        clusters: list[str] = []
        current: list[str] = []

        for line in lines:
            if re.search(r"^(FAILED|FAIL|ERROR|error:|TS\d{4}:)", line.strip(), re.IGNORECASE):
                if current:
                    clusters.append("\n".join(current[:5]))
                    current = []
            if len(current) < 5:
                current.append(line.strip())

        if current:
            clusters.append("\n".join(current[:5]))

        # Limit to top 5 subproblems
        return clusters[:5] or [lines[0].strip() if lines else "Unspecified error"]
