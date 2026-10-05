"""Template engine for system prompts co-located in system_prompts/ directory."""

from __future__ import annotations

import platform
import re
from pathlib import Path
from typing import Any

_PROMPTS_DIR = Path(__file__).parent


def load_prompt_template(name: str = "harness") -> str:
    """Load a markdown prompt template from the system_prompts directory."""
    clean_name = name.removesuffix(".md")
    template_path = _PROMPTS_DIR / f"{clean_name}.md"
    if not template_path.is_file():
        raise FileNotFoundError(f"Template prompt not found at {template_path}")
    content = template_path.read_text(encoding="utf-8").strip()
    if clean_name != "common_knowledge_spec" and "{{ common_knowledge_spec }}" in content:
        spec_path = _PROMPTS_DIR / "common_knowledge_spec.md"
        if spec_path.is_file():
            content = content.replace("{{ common_knowledge_spec }}", spec_path.read_text(encoding="utf-8").strip())
    return content


def render_prompt_template(template: str, context: dict[str, str]) -> str:
    """Render template replacing {{ key }} and {{key}} with context values."""

    def replacer(match: re.Match) -> str:
        key = match.group(1).strip()
        return context.get(key, match.group(0))

    return re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", replacer, template)


def format_tools_summary(registered_tools: dict[str, Any] | list[str]) -> str:
    """Format available tools into a categorized overview, omitting redundant schemas."""
    categories: dict[str, list[str]] = {
        "Filesystem": ["read_file", "write_file", "overwrite_file", "delete_file"],
        "Exploration": ["search_file", "list_dir"],
        "Terminal Execution": ["run_command", "manage_task"],
        "Environment & Interaction": ["get_status", "ask_question"],
        "Knowledge Base": ["query_knowledge", "record_learning"],
        "Delegation & Orchestration": ["invoke_subagent", "shorten_tool_output"],
    }
    tool_keys: set[str] = set(registered_tools.keys()) if isinstance(registered_tools, dict) else set(registered_tools)

    lines = ["Available tools (parameter schemas provided via function calling):"]
    accounted: set[str] = set()
    for cat, tool_names in categories.items():
        present = [f"`{name}`" for name in tool_names if name in tool_keys]
        if present:
            lines.append(f"- **{cat}**: {', '.join(present)}")
            accounted.update(tool_names)

    extra = [f"`{name}`" for name in tool_keys if name not in accounted]
    if extra:
        lines.append(f"- **Additional Tools**: {', '.join(extra)}")

    return "\n".join(lines)


def format_user_rules(agents_rules: dict[str, str]) -> str:
    """Format developer global and project repository rules."""
    rule_texts: list[str] = []
    if "global" in agents_rules and agents_rules["global"].strip():
        rule_texts.append(f"### Developer Global Guidelines\n{agents_rules['global'].strip()}")
    if "project" in agents_rules and agents_rules["project"].strip():
        rule_texts.append(f"### Project Repository Guidelines\n{agents_rules['project'].strip()}")

    if not rule_texts:
        return "No custom user rules specified."
    return "\n\n".join(rule_texts)


def format_workspace_info(workspace_root: Path) -> str:
    """Format workspace metadata and operating system details."""
    os_name = platform.system().lower()
    return (
        f"- **Workspace Name**: `{workspace_root.name}`\n"
        f"- **Workspace Path**: `{workspace_root.resolve()}`\n"
        f"- **Operating System**: `{os_name}`"
    )


def assemble_harness_system_prompt(
    workspace_root: Path,
    registered_tools: dict[str, Any] | list[str],
    agents_rules: dict[str, str],
    template_name: str = "harness",
) -> str:
    """Assemble complete Zone 1 system prompt using template engine."""
    template = load_prompt_template(template_name)
    context = {
        "workspace_info": format_workspace_info(workspace_root),
        "user_rules": format_user_rules(agents_rules),
        "available_tools_summary": format_tools_summary(registered_tools),
    }
    return render_prompt_template(template, context)
