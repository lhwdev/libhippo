"""Automatic discovery of .libhippo, AGENTS.md, .agents skills, and global MCP servers."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ruamel.yaml import YAML

from libhippo.runner.config import GlobalMcpConfig, HarnessConfig, SkillDefinition
from libhippo.runner.env import load_env_hierarchy


class ResourceDiscovery:
    """Discovers project knowledge, conventions, skills, and MCP configuration."""

    def __init__(self, workspace_root: Path, config: HarnessConfig | None = None) -> None:
        self.workspace_root = workspace_root.resolve()
        self.config = config or HarnessConfig(workspace_root=self.workspace_root)

    def discover_libhippo_dir(self) -> Path | None:
        """Locate project-scoped .libhippo directory if present."""
        candidate = self.workspace_root / ".libhippo"
        return candidate if candidate.is_dir() else None

    def discover_agents_markdown(self) -> dict[str, str]:
        """Discover and load Project and Global AGENTS.md rules."""
        rules: dict[str, str] = {}

        # 1. Global user rules
        global_paths = [self.config.user_config_dir / "AGENTS.md"]
        for p in global_paths:
            if p.is_file():
                try:
                    rules["global"] = p.read_text(encoding="utf-8")
                    break
                except OSError:
                    pass

        # 2. Project rules (higher priority)
        project_agent_path = self.workspace_root / "AGENTS.md"
        if project_agent_path.is_file():
            try:
                rules["project"] = project_agent_path.read_text(encoding="utf-8")
            except OSError:
                pass

        return rules

    def discover_skills(self) -> dict[str, SkillDefinition]:
        """Discover skills conforming to .agents/skills/*/SKILL.md convention."""
        skills: dict[str, SkillDefinition] = {}

        search_dirs = [
            # User global skills
            Path.home() / ".agents" / "skills",
            self.config.user_config_dir / "skills",
            # Project local skills
            self.workspace_root / ".agents" / "skills",
        ]

        for s_dir in search_dirs:
            if not s_dir.is_dir():
                continue
            for skill_file in s_dir.glob("*/SKILL.md"):
                try:
                    skill = self._parse_skill_file(skill_file)
                    if skill:
                        skills[skill.name] = skill
                except Exception:
                    pass

        return skills

    def _parse_skill_file(self, skill_file: Path) -> SkillDefinition | None:
        """Extract YAML frontmatter and markdown body from SKILL.md."""
        content = skill_file.read_text(encoding="utf-8")
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
        if match:
            frontmatter_raw = match.group(1)
            body = match.group(2).strip()
            try:
                yaml_parser = YAML(typ="safe")
                data = yaml_parser.load(frontmatter_raw) or {}
            except Exception:  # noqa: BLE001
                data = {}
        else:
            data = {}
            body = content.strip()

        name = data.get("name") or skill_file.parent.name
        description = data.get("description") or ""

        return SkillDefinition(
            name=name,
            description=description,
            skill_path=skill_file.parent,
            system_prompt=body,
            tool_dependencies=data.get("tools") or [],
        )

    def discover_global_mcp(self) -> GlobalMcpConfig:
        """Load global MCP server configuration from ~/.config/libhippo/mcp.json."""
        mcp_path = self.config.get_mcp_config_path()
        if mcp_path.is_file():
            try:
                data = json.loads(mcp_path.read_text(encoding="utf-8"))
                return GlobalMcpConfig.model_validate(data)
            except Exception:
                pass
        return GlobalMcpConfig()

    def load_env(self, environment: str | None = None, override: bool = False) -> list[Path]:
        """Load env from available combination of .env.local, .env.<env>.local, .env.<env>, .env.

        Checks workspace_root as primary base directory, with user_config_dir as fallback.
        """
        extra_dirs = [self.config.user_config_dir]
        return load_env_hierarchy(
            base_dir=self.workspace_root,
            environment=environment,
            override=override,
            extra_dirs=extra_dirs,
        )
