"""Codebase exploration tools: pure Python search_file and list_dir."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from libhippo.runner.tools.base import BaseToolSuite, ToolExecutionError
from libhippo.runner.triage import OutputTriage
from libhippo.runner.types import ToolDefinition


class GitIgnoreMatcher:
    """Parses and matches .gitignore rules natively in pure Python."""

    def __init__(self, root_dir: Path, target_scope: Path | None = None) -> None:
        self.root_dir = root_dir.resolve()
        self.target_scope = target_scope.resolve() if target_scope else self.root_dir
        self.rules: list[tuple[str, bool, bool, Path]] = []  # (pattern, is_negation, is_dir_only, base_path)
        self._load_gitignores()
        self._scope_ignored_by_parents = self._check_scope_ignored_by_parents()

    def _load_gitignores(self) -> None:
        """Scan and parse all .gitignore files under root_dir."""
        for root, _, files in os.walk(self.root_dir):
            if ".gitignore" in files:
                base_path = Path(root).resolve()
                gi_file = base_path / ".gitignore"
                try:
                    for line in gi_file.read_text(encoding="utf-8", errors="replace").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        is_negation = line.startswith("!")
                        if is_negation:
                            line = line[1:].strip()
                        is_dir_only = line.endswith("/")
                        if is_dir_only:
                            line = line[:-1]
                        self.rules.append((line, is_negation, is_dir_only, base_path))
                except OSError:
                    continue

    def _check_scope_ignored_by_parents(self) -> bool:
        """Check if target_scope itself is ignored by a .gitignore rule from an ancestor directory."""
        if self.target_scope == self.root_dir:
            return False
        curr = self.target_scope
        while curr != self.root_dir and curr != curr.parent:
            for pattern, is_negation, is_dir_only, base_path in self.rules:
                if not curr.is_relative_to(base_path) or curr == base_path:
                    continue
                try:
                    rel = curr.relative_to(base_path).as_posix()
                except ValueError:
                    continue

                matched = False
                if "/" in pattern:
                    pat = pattern.lstrip("/")
                    if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, f"**/{pat}"):
                        matched = True
                else:
                    if fnmatch.fnmatch(curr.name, pattern) or fnmatch.fnmatch(rel, f"**/{pattern}"):
                        matched = True

                if matched and not is_negation:
                    return True
            curr = curr.parent
        return False

    def is_ignored(self, path: Path, is_dir: bool = False) -> bool:
        """Check if path is ignored by loaded gitignore rules."""
        resolved = path.resolve()
        # Always ignore .git directory
        try:
            rel_to_root = resolved.relative_to(self.root_dir).as_posix()
        except ValueError:
            return False

        if rel_to_root == ".git" or rel_to_root.startswith(".git/"):
            return True

        is_inside_scope = (resolved == self.target_scope or resolved.is_relative_to(self.target_scope))

        ignored = False
        for pattern, is_negation, is_dir_only, base_path in self.rules:
            if is_dir_only and not is_dir:
                continue

            # If target_scope is itself an ignored folder (e.g. .venv, node_modules),
            # ignore rules from parents above target_scope do not apply inside target_scope.
            if self._scope_ignored_by_parents and is_inside_scope:
                if not base_path.is_relative_to(self.target_scope):
                    continue

            try:
                rel = resolved.relative_to(base_path).as_posix()
            except ValueError:
                continue

            # Handle glob pattern matching
            matched = False
            if "/" in pattern:
                pat = pattern.lstrip("/")
                if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, f"**/{pat}"):
                    matched = True
            else:
                if fnmatch.fnmatch(resolved.name, pattern) or fnmatch.fnmatch(rel, f"**/{pattern}"):
                    matched = True

            if matched:
                ignored = not is_negation

        return ignored


class ExplorationTools(BaseToolSuite):
    """Exploration tools implemented in pure Python without external binary dependencies."""

    async def search_file(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        no_ignore: bool = False,
        hidden: bool = False,
    ) -> str:
        """Search code files for pattern in pure Python, respecting .gitignore by default."""
        target_dir = self.resolve_path(path)
        if not target_dir.exists():
            raise ToolExecutionError(f"Directory or file not found: '{path}'")

        await self.check_approval_if_needed("search_file", {"pattern": pattern, "path": path})

        # Compile regex pattern or fallback to literal substring search
        try:
            regex = re.compile(pattern)
        except re.error:
            regex = re.compile(re.escape(pattern))

        ignore_matcher = GitIgnoreMatcher(self.workspace_root, target_scope=target_dir) if not no_ignore else None

        matches: list[str] = []
        max_matches = 1000

        # Case 1: Target is a single file
        if target_dir.is_file():
            self._search_single_file(target_dir, regex, matches, max_matches)
        else:
            # Case 2: Target is a directory - traverse files
            for root, dirs, files in os.walk(target_dir):
                root_path = Path(root)

                # Filter directories
                filtered_dirs: list[str] = []
                for d in dirs:
                    d_path = root_path / d
                    if not hidden and d.startswith(".") and not (target_dir.name.startswith(".") and d_path.is_relative_to(target_dir)):
                        continue
                    if ignore_matcher and ignore_matcher.is_ignored(d_path, is_dir=True):
                        continue
                    filtered_dirs.append(d)
                dirs[:] = filtered_dirs

                # Filter and search files
                for f in files:
                    if not hidden and f.startswith(".") and not (target_dir.name.startswith(".") and (root_path / f).is_relative_to(target_dir)):
                        continue
                    if glob and not fnmatch.fnmatch(f, glob):
                        continue

                    f_path = root_path / f
                    if ignore_matcher and ignore_matcher.is_ignored(f_path, is_dir=False):
                        continue

                    self._search_single_file(f_path, regex, matches, max_matches)
                    if len(matches) >= max_matches:
                        break
                if len(matches) >= max_matches:
                    break

        if not matches:
            return "No matches found."

        return "\n".join(matches)

    def _search_single_file(
        self,
        file_path: Path,
        regex: re.Pattern[str],
        matches: list[str],
        max_matches: int,
    ) -> None:
        """Search lines in single file and append formatted matches."""
        try:
            rel_path = file_path.relative_to(self.workspace_root).as_posix()
        except ValueError:
            rel_path = file_path.as_posix()

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, start=1):
                    line_clean = line.rstrip("\r\n")
                    if regex.search(line_clean):
                        matches.append(f"{rel_path}:{lineno}:{line_clean}")
                        if len(matches) >= max_matches:
                            return
        except (OSError, UnicodeError):
            return

    async def list_dir(
        self,
        path: str | None = None,
        depth: int = 2,
        show_hidden: bool = False,
    ) -> str:
        """Programmatic directory tree traversal in pure Python."""
        target_dir = self.resolve_path(path)
        if not target_dir.is_dir():
            raise ToolExecutionError(f"Directory not found: '{path}'")

        await self.check_approval_if_needed("list_dir", {"path": path})

        if target_dir == self.workspace_root:
            root_label = "./"
        else:
            try:
                rel = target_dir.relative_to(self.workspace_root).as_posix()
                root_label = f"{rel}/"
            except ValueError:
                root_label = f"{target_dir.name}/"

        ignore_matcher = GitIgnoreMatcher(self.workspace_root, target_scope=target_dir)

        lines: list[str] = [root_label]
        self._build_tree(target_dir, "", 1, depth, show_hidden, lines, ignore_matcher)
        return "\n".join(lines)

    def _build_tree(
        self,
        current: Path,
        prefix: str,
        current_depth: int,
        max_depth: int,
        show_hidden: bool,
        lines: list[str],
        ignore_matcher: GitIgnoreMatcher,
    ) -> None:
        """Recursively construct directory tree up to max_depth."""
        if current_depth > max_depth:
            return

        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return

        visible: list[Path] = []
        for e in entries:
            if not show_hidden:
                if e.name == ".git":
                    continue
                if e.name.startswith(".") and not ignore_matcher.is_ignored(e, is_dir=e.is_dir()):
                    continue
            visible.append(e)

        count = len(visible)

        for idx, entry in enumerate(visible):
            is_last = idx == count - 1
            connector = "└── " if is_last else "├── "
            child_prefix = "    " if is_last else "│   "

            is_dir = entry.is_dir()
            is_ignored = ignore_matcher.is_ignored(entry, is_dir=is_dir)

            if is_dir:
                if is_ignored:
                    lines.append(f"{prefix}{connector}{entry.name}/ (ignored)")
                else:
                    lines.append(f"{prefix}{connector}{entry.name}/")
                    self._build_tree(entry, prefix + child_prefix, current_depth + 1, max_depth, show_hidden, lines, ignore_matcher)
            else:
                if is_ignored:
                    lines.append(f"{prefix}{connector}{entry.name} (ignored)")
                else:
                    lines.append(f"{prefix}{connector}{entry.name}")

    def get_tool_definitions(self) -> dict[str, ToolDefinition]:
        """Return ToolDefinition schemas for exploration tools."""
        return {
            "search_file": ToolDefinition(
                name="search_file",
                description="Search code files for pattern in pure Python (respects .gitignore and glob filters).",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Search pattern or regular expression"},
                        "path": {"type": "string", "description": "Subdirectory or file to search"},
                        "glob": {"type": "string", "description": "File glob filter (e.g. *.py, *.ts)"},
                        "no_ignore": {"type": "boolean", "description": "Set true to search files matched by .gitignore"},
                        "hidden": {"type": "boolean", "description": "Set true to search hidden files and directories"},
                    },
                    "required": ["pattern"],
                },
                handler=self.search_file,
            ),
            "list_dir": ToolDefinition(
                name="list_dir",
                description="List directory tree structure up to depth. Displays ignored folders as '(ignored)' without recursing.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path to list"},
                        "depth": {"type": "integer", "description": "Max recursion depth (default: 2)"},
                        "show_hidden": {"type": "boolean", "description": "Set true to show hidden dotfiles"},
                    },
                },
                handler=self.list_dir,
            ),
        }
