"""Unified configuration module for LibHippo."""

from libhippo.config.models import (
    AgentRole,
    DEFAULT_AGENT_MODELS,
    DEFAULT_BOOK_KEEPER_CONFIG,
    DEFAULT_CHECKER_CONFIG,
    DEFAULT_CURATOR_CONFIG,
    DEFAULT_HARNESS_MODEL,
    DEFAULT_OPENAI_MODEL_INFO,
    DEFAULT_TASK_SOLVER_CONFIG,
    DEFAULT_VERIFIER_CONFIG,
    ModelConfig,
    ModelProvider,
)
from libhippo.config.paths import (
    DEFAULT_GLOBAL_SKILLS_DIR,
    DEFAULT_USER_CONFIG_DIR,
    get_cache_dir,
    get_global_agents_md_path,
    get_global_skills_dirs,
    get_mcp_config_path,
    get_project_data_dir,
    get_user_config_dir,
    get_user_knowledge_dir,
)

__all__ = [
    "AgentRole",
    "ModelProvider",
    "ModelConfig",
    "DEFAULT_OPENAI_MODEL_INFO",
    "DEFAULT_TASK_SOLVER_CONFIG",
    "DEFAULT_BOOK_KEEPER_CONFIG",
    "DEFAULT_CURATOR_CONFIG",
    "DEFAULT_CHECKER_CONFIG",
    "DEFAULT_VERIFIER_CONFIG",
    "DEFAULT_AGENT_MODELS",
    "DEFAULT_HARNESS_MODEL",
    "DEFAULT_USER_CONFIG_DIR",
    "DEFAULT_GLOBAL_SKILLS_DIR",
    "get_user_config_dir",
    "get_user_knowledge_dir",
    "get_project_data_dir",
    "get_cache_dir",
    "get_global_skills_dirs",
    "get_global_agents_md_path",
    "get_mcp_config_path",
]
