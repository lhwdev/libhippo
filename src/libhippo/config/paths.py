"""Directory and path resolution for LibHippo.

All path resolver functions are stateless and read environment variables
directly from os.environ (or an optional explicit environment mapping).
Relative paths for subdirectories (such as cache, knowledge, skills, mcp.json)
are resolved relative to get_user_config_dir().
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

DEFAULT_USER_CONFIG_DIR = Path.home() / ".config" / "libhippo"
DEFAULT_GLOBAL_SKILLS_DIR = Path.home() / ".agents" / "skills"


def expand_env_vars(val: str, env_map: Mapping[str, str]) -> str:
    """Expand environment variables using os.path.expandvars and env_map."""
    expanded = os.path.expandvars(val)
    if "$" in expanded:
        for k, v in env_map.items():
            expanded = expanded.replace(f"${{{k}}}", str(v)).replace(f"${k}", str(v))
    return expanded


def resolve_subpath(
    val: str | Path,
    base_dir: Path,
    env_map: Mapping[str, str] | None = None,
) -> Path:
    """Resolve subpath: expand vars and user tildes; relative paths resolve to base_dir."""
    env = os.environ if env_map is None else env_map
    expanded = expand_env_vars(str(val), env)
    p = Path(expanded).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (base_dir / p).resolve()


def get_user_config_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return user configuration directory (default: ~/.config/libhippo).

    Stateless lookup configurable via LIBHIPPO_CONFIG_DIR or LIBHIPPO_USER_CONFIG_DIR.
    Relative paths are resolved relative to Path.home().
    """
    env_map = os.environ if env is None else env
    env_val = env_map.get("LIBHIPPO_CONFIG_DIR") or env_map.get("LIBHIPPO_USER_CONFIG_DIR")
    if env_val:
        return resolve_subpath(env_val, base_dir=Path.home(), env_map=env_map)
    return DEFAULT_USER_CONFIG_DIR.resolve()


def get_user_knowledge_dir(
    user_config_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return global user knowledge mount root (default: <user_config_dir>/knowledge)."""
    env_map = os.environ if env is None else env
    base = user_config_dir or get_user_config_dir(env=env)
    env_val = env_map.get("LIBHIPPO_KNOWLEDGE_DIR") or env_map.get("LIBHIPPO_USER_KNOWLEDGE_DIR")
    if env_val:
        return resolve_subpath(env_val, base_dir=base, env_map=env_map)
    return (base / "knowledge").resolve()


def get_project_data_dir(
    workspace_root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return project-local data directory (default: <workspace_root>/.libhippo)."""
    env_map = os.environ if env is None else env
    base = (workspace_root or Path.cwd()).resolve()
    env_val = env_map.get("LIBHIPPO_PROJECT_DIR") or env_map.get("LIBHIPPO_DATA_DIR")
    if env_val:
        return resolve_subpath(env_val, base_dir=base, env_map=env_map)
    return (base / ".libhippo").resolve()


def get_cache_dir(
    workspace_root: Path | None = None,
    user_config_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return cache directory for embeddings, catalogs, and logs."""
    env_map = os.environ if env is None else env
    base = user_config_dir or get_user_config_dir(env=env)
    env_val = env_map.get("LIBHIPPO_CACHE_DIR")
    if env_val:
        return resolve_subpath(env_val, base_dir=base, env_map=env_map)
    if workspace_root:
        return (get_project_data_dir(workspace_root=workspace_root, env=env) / "cache").resolve()
    return (base / "cache").resolve()


def get_global_skills_dirs(
    user_config_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> list[Path]:
    """Return list of global skills search directories."""
    env_map = os.environ if env is None else env
    base = user_config_dir or get_user_config_dir(env=env)
    env_val = env_map.get("LIBHIPPO_SKILLS_DIR")
    dirs: list[Path] = []
    if env_val:
        for item in env_val.split(os.pathsep):
            item = item.strip()
            if item:
                dirs.append(resolve_subpath(item, base_dir=base, env_map=env_map))
    else:
        dirs.append(DEFAULT_GLOBAL_SKILLS_DIR.resolve())
        dirs.append((base / "skills").resolve())
    return dirs


def get_global_agents_md_path(
    user_config_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    env_map = os.environ if env is None else env
    base = user_config_dir or get_user_config_dir(env=env)
    env_val = env_map.get("LIBHIPPO_AGENTS_MD")
    if env_val:
        return resolve_subpath(env_val, base_dir=base, env_map=env_map)
    return (base / "AGENTS.md").resolve()


def get_mcp_config_path(
    user_config_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    env_map = os.environ if env is None else env
    base = user_config_dir or get_user_config_dir(env=env)
    env_val = env_map.get("LIBHIPPO_MCP_CONFIG")
    if env_val:
        return resolve_subpath(env_val, base_dir=base, env_map=env_map)
    return (base / "mcp.json").resolve()
