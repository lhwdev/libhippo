"""Configuration models for the General Coding Agent Harness."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from libhippo.config.models import DEFAULT_TASK_SOLVER_CONFIG, ModelConfig
from libhippo.config.paths import (
    get_mcp_config_path,
    get_user_config_dir,
    get_user_knowledge_dir,
)
from libhippo.runner.env import load_env_hierarchy
from libhippo.runner.types import ExecutionMode


class SecurityRuleList(BaseModel):
    """Fine-grained allow/deny/ask policy lists for an operation category."""

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)


class ProjectSecurityPolicy(BaseModel):
    """Project-level security policy stored in user dir (~/.config/libhippo/projects/)."""

    project_id: str
    workspace_root: Path
    read_file: SecurityRuleList = Field(default_factory=SecurityRuleList)
    write_file: SecurityRuleList = Field(default_factory=SecurityRuleList)
    command: SecurityRuleList = Field(default_factory=SecurityRuleList)
    allow_network: bool = False
    allowed_domains: list[str] = Field(default_factory=list)

    @classmethod
    def create_default(cls, workspace_root: Path, project_id: str | None = None) -> ProjectSecurityPolicy:
        """Generate default security policy scoped to the workspace root."""
        pid = project_id or compute_project_id(workspace_root)
        return cls(
            project_id=pid,
            workspace_root=workspace_root.resolve(),
            read_file=SecurityRuleList(
                allow=["**"],
                deny=["**/.env*", "**/secrets/**", "**/*.pem", "**/*.key"],
                ask=[],
            ),
            write_file=SecurityRuleList(
                allow=["**"],
                deny=[".git/**", "package-lock.json", "uv.lock"],
                ask=[],
            ),
            command=SecurityRuleList(
                allow=["uv run pytest*", "git status", "git diff*", "rg *", "ls *"],
                deny=["rm -rf /*", "curl * | bash", "wget * | bash"],
                ask=["git push*", "npm publish*"],
            ),
            allow_network=False,
            allowed_domains=[],
        )


class McpServerConfig(BaseModel):
    """Global Model Context Protocol (MCP) server configuration."""

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class GlobalMcpConfig(BaseModel):
    """Root schema for ~/.config/libhippo/mcp.json."""

    mcpServers: dict[str, McpServerConfig] = Field(default_factory=dict)


class SkillDefinition(BaseModel):
    """Discovered skill following .agents convention."""

    name: str
    description: str
    skill_path: Path
    system_prompt: str
    tool_dependencies: list[str] = Field(default_factory=list)


def compute_project_id(workspace_root: Path) -> str:
    """Generate deterministic project slug/hash from workspace root path."""
    canonical = str(workspace_root.resolve()).lower()
    h = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    slug = workspace_root.resolve().name or "root"
    return f"{slug}-{h}"


class HarnessConfig(BaseModel):
    """Runtime configuration for the General Coding Agent Harness."""

    model: ModelConfig = Field(default_factory=lambda: DEFAULT_TASK_SOLVER_CONFIG.model_copy())
    workspace_root: Path = Field(default_factory=Path.cwd)
    mode: ExecutionMode = ExecutionMode.DEFAULT
    soft_token_watermark: int = 60000
    hard_token_limit: int = 100000
    compaction_target_tokens: int = 40000
    max_turns: int = 16
    command_timeout_ms: int = 30000
    allow_sandbox_bypass: bool = False
    transport_mode: Literal["websocket", "http"] = "websocket"
    enable_http_fallback: bool = True
    user_config_dir: Path = Field(default_factory=get_user_config_dir)

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        self.workspace_root = self.workspace_root.expanduser().resolve()
        load_env_hierarchy(base_dir=self.workspace_root)
        if "user_config_dir" not in self.model_fields_set:
            self.user_config_dir = get_user_config_dir()
        else:
            self.user_config_dir = self.user_config_dir.expanduser().resolve()

    @property
    def model_name(self) -> str:
        """Return primary model name string."""
        return self.model.model

    def get_project_id(self) -> str:
        """Derive project identifier for this workspace root."""
        return compute_project_id(self.workspace_root)

    def get_project_config_path(self) -> Path:
        """Return path to project policy file in user config directory."""
        return self.user_config_dir / "projects" / f"{self.get_project_id()}.json"

    def get_conversations_dir(self) -> Path:
        """Return path to project conversations directory in user config directory."""
        return self.user_config_dir / "projects" / self.get_project_id() / "conversations"

    def get_mcp_config_path(self) -> Path:
        """Return path to global MCP configuration file."""
        return get_mcp_config_path(self.user_config_dir)

    def get_knowledge_dir(self) -> Path:
        """Return path to user knowledge directory."""
        return get_user_knowledge_dir(self.user_config_dir)
