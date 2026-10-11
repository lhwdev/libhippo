"""Safe JSON serialization utilities for web and websocket payloads."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
from typing import Any


def _json_default(o: Any) -> Any:
    """Safe fallback serializer for non-primitive Python types."""
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, (set, frozenset)):
        return list(o)
    if hasattr(o, "isoformat") and callable(o.isoformat):
        return o.isoformat()
    if is_dataclass(o) and not isinstance(o, type):
        return asdict(o)
    if hasattr(o, "model_dump") and callable(o.model_dump):
        return o.model_dump()
    if hasattr(o, "dict") and callable(o.dict):
        return o.dict()
    if isinstance(o, (bytes, bytearray)):
        return o.decode("utf-8", errors="replace")
    if isinstance(o, Exception):
        return str(o)
    if hasattr(o, "__dict__"):
        try:
            return vars(o)
        except Exception:
            return str(o)
    return str(o)


def safe_json_dumps(data: Any, **kwargs: Any) -> str:
    """Safely serialize any data structure to a JSON string without raising TypeError."""
    if "default" not in kwargs:
        kwargs["default"] = _json_default
    return json.dumps(data, **kwargs)
