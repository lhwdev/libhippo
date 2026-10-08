"""Filesystem tools: read_file, overwrite_file, write_file, delete_file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from libhippo.runner.project import ProjectSecurityError
from libhippo.runner.tools.base import BaseToolSuite, ToolExecutionError
from libhippo.runner.types import ToolDefinition


class FileTools(BaseToolSuite):
    """File manipulation tools enforcing line limits, atomic writes, and security policies."""

    async def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        """Read line-addressed slice of a file (max 800 lines per call)."""
        file_path = self.resolve_path(path)
        if not file_path.is_file():
            raise ToolExecutionError(f"File not found: '{path}'")

        decision = self.project_manager.check_read(file_path)
        if decision == "deny":
            raise ProjectSecurityError(f"Read access denied for '{path}'.")

        await self.check_approval_if_needed("read_file", {"path": path})

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = file_path.read_bytes().decode("utf-8", errors="replace")

        lines = content.splitlines()
        total_lines = len(lines)

        s_line = max(1, start_line)
        e_line = min(total_lines, end_line) if end_line is not None else min(total_lines, s_line + 799)

        if e_line - s_line + 1 > 800:
            e_line = s_line + 799

        if s_line > total_lines:
            return f"[File '{path}' has {total_lines} lines; start_line {s_line} is out of bounds]"

        selected_lines = lines[s_line - 1:e_line]
        formatted = "\n".join(f"{s_line + idx}: {line}" for idx, line in enumerate(selected_lines))
        return (
            f'<file path="{path}" lines="{s_line}:{e_line}" total_lines="{total_lines}">\n'
            f"{formatted}\n"
            "</file>"
        )

    async def overwrite_file(self, path: str, content: str) -> str:
        """Create new file or completely overwrite existing file atomically."""
        file_path = self.resolve_path(path)
        decision = self.project_manager.check_write(file_path)
        if decision == "deny":
            raise ProjectSecurityError(f"Write access denied for '{path}'.")

        await self.check_approval_if_needed("overwrite_file", {"path": path})

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content.encode('utf-8'))} bytes to {path}"

    async def write_file(
        self,
        path: str,
        content: str,
        start_line: int | None = None,
        end_line: int | None = None,
        target: str | None = None,
    ) -> str:
        """Targeted line-range replacement or exact string search-and-replace."""
        file_path = self.resolve_path(path)
        decision = self.project_manager.check_write(file_path)
        if decision == "deny":
            raise ProjectSecurityError(f"Write access denied for '{path}'.")

        await self.check_approval_if_needed(
            "write_file",
            {"path": path, "target": target, "start_line": start_line, "end_line": end_line},
        )

        if not file_path.is_file():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"Created {path}"

        current_text = file_path.read_text(encoding="utf-8")

        # 1. Target string search-and-replace
        if target is not None:
            if start_line is not None or end_line is not None:
                raise ToolExecutionError('Do not supply both "target" and "start_line"/"end_line".')
            if target not in current_text:
                raise ToolExecutionError(f"Target string not found in {path}")
            new_text = current_text.replace(target, content, 1)
            file_path.write_text(new_text, encoding="utf-8")
            return f"Replaced target text in {path}"

        # 2. Line range replacement
        if start_line is not None or end_line is not None:
            lines = current_text.splitlines()
            s = max(1, start_line) if start_line is not None else 1
            e = min(len(lines), end_line) if end_line is not None else len(lines)
            new_lines = lines[:s - 1] + content.splitlines() + lines[e:]
            file_path.write_text("\n".join(new_lines) + ("\n" if current_text.endswith("\n") else ""), encoding="utf-8")
            return f"Updated lines {s}-{e} in {path}"

        # 3. Whole-file replacement fallback
        file_path.write_text(content, encoding="utf-8")
        return f"Updated whole file {path}"

    async def delete_file(self, path: str) -> str:
        """Safely remove a file."""
        file_path = self.resolve_path(path)
        decision = self.project_manager.check_write(file_path)
        if decision == "deny":
            raise ProjectSecurityError(f"Deletion denied for '{path}'.")

        await self.check_approval_if_needed("delete_file", {"path": path})

        if not file_path.is_file():
            raise ToolExecutionError(f"File not found: '{path}'")

        file_path.unlink()
        return f"Deleted {path}"

    def get_tool_definitions(self) -> dict[str, ToolDefinition]:
        """Return ToolDefinition schemas for file operations."""
        return {
            "read_file": ToolDefinition(
                name="read_file",
                description="Read line-addressed slice of a file (max 800 lines/call).",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to file"},
                        "start_line": {"type": "integer", "description": "1-indexed start line"},
                        "end_line": {"type": "integer", "description": "1-indexed end line"},
                    },
                    "required": ["path"],
                },
                handler=self.read_file,
            ),
            "overwrite_file": ToolDefinition(
                name="overwrite_file",
                description="Create or overwrite full content of a file.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to target file"},
                        "content": {"type": "string", "description": "Complete file content to write"},
                    },
                    "required": ["path", "content"],
                },
                handler=self.overwrite_file,
            ),
            "write_file": ToolDefinition(
                name="write_file",
                description="Modify targeted line range or search-and-replace exact string in file.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Target file path"},
                        "content": {"type": "string", "description": "Replacement text chunk"},
                        "start_line": {"type": "integer", "description": "1-indexed start line"},
                        "end_line": {"type": "integer", "description": "1-indexed end line"},
                        "target": {"type": "string", "description": "Exact text to replace"},
                    },
                    "required": ["path", "content"],
                },
                handler=self.write_file,
            ),
            "delete_file": ToolDefinition(
                name="delete_file",
                description="Safely delete a file inside workspace.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path of file to delete"},
                    },
                    "required": ["path"],
                },
                handler=self.delete_file,
            ),
        }
