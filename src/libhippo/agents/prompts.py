"""Programmatic loader for LibHippo agent system prompts stored in markdown files.

Each agent's system prompt is authored in a dedicated markdown file under `prompts/`:
- `book_keeper.md`
- `curator.md`
- `verifier.md`
- `task_solver.md`

They are read programmatically and cached in-memory.
"""

from __future__ import annotations

import functools
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "system_prompts"


@functools.lru_cache(maxsize=16)
def load_prompt_file(name: str) -> str:
    """Read a markdown prompt file from prompts/ or runner/ directory."""
    clean_name = name.removesuffix(".md")
    path = _PROMPTS_DIR / f"{clean_name}.md"
    if path.is_file():
        content = path.read_text(encoding="utf-8").strip()
    else:
        runner_path = Path(__file__).resolve().parent.parent / "runner" / f"{clean_name}.md"
        if runner_path.is_file():
            content = runner_path.read_text(encoding="utf-8").strip()
        else:
            raise FileNotFoundError(f"Prompt file not found at {path}")

    if clean_name != "common_knowledge_spec" and "{{ common_knowledge_spec }}" in content:
        spec = load_prompt_file("common_knowledge_spec")
        content = content.replace("{{ common_knowledge_spec }}", spec)
    return content


def get_agent_system_prompt(role: str) -> str:
    """Retrieve the system prompt for a specified agent role programmatically."""
    import re

    # Convert CamelCase (e.g. BookKeeperAgent) to snake_case (book_keeper_agent)
    clean = re.sub(r"(?<!^)(?=[A-Z])", "_", role).lower().replace("-", "_")
    clean = clean.replace("_agent", "").replace("agent", "").strip("_")
    aliases = {
        "bookkeeper": "book_keeper",
        "tasksolver": "task_solver",
    }
    role_key = aliases.get(clean, clean)

    valid_roles = {
        "book_keeper",
        "curator",
        "verifier",
        "task_solver",
        "harness",
        "harvest_sidecar",
        "common_knowledge_spec",
    }
    if role_key not in valid_roles:
        raise ValueError(f"Unknown agent role '{role}'. Available: {sorted(valid_roles)}")
    return load_prompt_file(role_key)



# Convenience module-level properties / accessors
def __getattr__(name: str) -> str:
    if name == "BOOK_KEEPER_SYSTEM_PROMPT":
        return load_prompt_file("book_keeper")
    if name == "CURATOR_SYSTEM_PROMPT":
        return load_prompt_file("curator")
    if name == "VERIFIER_SYSTEM_PROMPT":
        return load_prompt_file("verifier")
    if name == "TASK_SOLVER_SYSTEM_PROMPT":
        return load_prompt_file("task_solver")
    if name == "HARNESS_SYSTEM_PROMPT":
        return load_prompt_file("harness")
    if name == "HARVEST_SIDECAR_PROMPT":
        return load_prompt_file("harvest_sidecar")
    if name == "COMMON_KNOWLEDGE_SPEC":
        return load_prompt_file("common_knowledge_spec")
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
