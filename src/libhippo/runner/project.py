"""Project security policy manager stored in user config directory."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Literal

from libhippo.runner.config import HarnessConfig, ProjectSecurityPolicy, compute_project_id


class ProjectSecurityError(PermissionError):
    """Raised when an operation violates the project security policy."""


class ProjectManager:
    """Manages project-scoped security policies and directory containment."""

    def __init__(self, workspace_root: Path, config: HarnessConfig | None = None) -> None:
        self.workspace_root = workspace_root.resolve()
        self.config = config or HarnessConfig(workspace_root=self.workspace_root)
        self.project_id = compute_project_id(self.workspace_root)
        self.policy_file = self.config.get_project_config_path()
        self.policy = self.load_or_create_policy()

    def load_or_create_policy(self) -> ProjectSecurityPolicy:
        """Load project policy from user directory or create default if not existing."""
        if self.policy_file.exists():
            try:
                data = json.loads(self.policy_file.read_text(encoding="utf-8"))
                return ProjectSecurityPolicy.model_validate(data)
            except Exception:
                pass
        policy = ProjectSecurityPolicy.create_default(
            workspace_root=self.workspace_root,
            project_id=self.project_id,
        )
        self.save_policy(policy)
        return policy

    def save_policy(self, policy: ProjectSecurityPolicy | None = None) -> None:
        """Persist project security policy to user directory."""
        pol = policy or self.policy
        try:
            self.policy_file.parent.mkdir(parents=True, exist_ok=True)
            self.policy_file.write_text(pol.model_dump_json(indent=2), encoding="utf-8")
        except OSError:
            pass

    def _relative_to_workspace(self, path: Path) -> str:
        """Return relative path string normalized for pattern matching."""
        resolved = path.resolve()
        try:
            return str(resolved.relative_to(self.workspace_root)).replace("\\", "/")
        except ValueError:
            return str(resolved).replace("\\", "/")

    def _matches_pattern(self, path: Path, rel_str: str, pattern: str) -> bool:
        """Match relative path or name against glob pattern, supporting leading **/."""
        if fnmatch.fnmatch(rel_str, pattern) or fnmatch.fnmatch(path.name, pattern):
            return True
        if pattern.startswith("**/"):
            stripped = pattern[3:]
            if fnmatch.fnmatch(rel_str, stripped) or fnmatch.fnmatch(path.name, stripped):
                return True
        return False

    def check_read(self, path: Path) -> Literal["allow", "deny", "ask"]:
        """Evaluate read permission for a path."""
        rel = self._relative_to_workspace(path)
        for pattern in self.policy.read_file.deny:
            if self._matches_pattern(path, rel, pattern):
                return "deny"
        for pattern in self.policy.read_file.ask:
            if self._matches_pattern(path, rel, pattern):
                return "ask"
        for pattern in self.policy.read_file.allow:
            if self._matches_pattern(path, rel, pattern):
                return "allow"
        return "ask"

    def check_write(self, path: Path) -> Literal["allow", "deny", "ask"]:
        """Evaluate write/delete permission for a path."""
        rel = self._relative_to_workspace(path)
        for pattern in self.policy.write_file.deny:
            if self._matches_pattern(path, rel, pattern):
                return "deny"
        for pattern in self.policy.write_file.ask:
            if self._matches_pattern(path, rel, pattern):
                return "ask"
        for pattern in self.policy.write_file.allow:
            if self._matches_pattern(path, rel, pattern):
                return "allow"
        return "ask"

    def check_command(self, command: str) -> Literal["allow", "deny", "ask"]:
        """Evaluate execution permission for a shell command string."""
        cmd_strip = command.strip()
        for pattern in self.policy.command.deny:
            if fnmatch.fnmatch(cmd_strip, pattern):
                return "deny"
        for pattern in self.policy.command.ask:
            if fnmatch.fnmatch(cmd_strip, pattern):
                return "ask"
        for pattern in self.policy.command.allow:
            if fnmatch.fnmatch(cmd_strip, pattern):
                return "allow"
        return "ask"

    def is_network_allowed(self) -> bool:
        """Check if outbound network connections are permitted."""
        return self.policy.allow_network
