"""Environment variable loading from .env hierarchies using python-dotenv."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv


def get_env_file_candidates(
    base_dir: Path | str = ".",
    environment: str | None = None,
) -> list[Path]:
    """Return prioritized candidate .env file paths according to standard 12-factor hierarchy.

    Order of priority (highest to lowest):
    1. .env.{environment}.local (e.g. .env.development.local, .env.production.local)
    2. .env.local (ignored in test environments typically, but loaded if present)
    3. .env.{environment}       (e.g. .env.development, .env.production)
    4. .env
    """
    root = Path(base_dir).expanduser().resolve()
    env_name = (
        environment
        or os.environ.get("LIBHIPPO_ENV")
        or os.environ.get("APP_ENV")
        or os.environ.get("NODE_ENV")
        or "development"
    ).lower()

    filenames: list[str] = [
        f".env.{env_name}.local",
        ".env.local",
        f".env.{env_name}",
        ".env",
    ]

    candidates: list[Path] = []
    seen: set[str] = set()
    for name in filenames:
        if name not in seen:
            seen.add(name)
            candidates.append(root / name)

    return candidates


def load_env_hierarchy(
    base_dir: Path | str = ".",
    environment: str | None = None,
    override: bool = False,
    extra_dirs: Sequence[Path | str] | None = None,
) -> list[Path]:
    """Load environment variables from available combination of .env files.

    Searches for .env.{env}.local, .env.local, .env.{env}, and .env in base_dir
    (and any extra_dirs). Existing environment variables take precedence unless
    override=True. Files are processed in priority order (highest first).
    Returns list of successfully loaded .env file paths.
    """
    dirs: list[Path] = [Path(base_dir).expanduser().resolve()]
    if extra_dirs:
        for d in extra_dirs:
            p = Path(d).expanduser().resolve()
            if p not in dirs:
                dirs.append(p)

    loaded: list[Path] = []
    for d in dirs:
        for candidate in get_env_file_candidates(d, environment=environment):
            if candidate.is_file():
                try:
                    # override=False preserves existing os.environ values and higher-priority files
                    if load_dotenv(dotenv_path=candidate, override=override):
                        loaded.append(candidate)
                except (OSError, PermissionError):
                    pass

    return loaded
