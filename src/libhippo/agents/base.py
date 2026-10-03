"""Modular base agent definitions for LibHippo."""

from __future__ import annotations

from abc import ABC


class BaseHippoAgent(ABC):
    """Abstract base class for all LibHippo agents.

    Ensures agents are modular and can be used standalone in application code,
    plugged into AutoGen 0.4 teams, or registered as tools.
    """

    def __init__(self, name: str, description: str = "") -> None:
        if not hasattr(self, "_name"):
            self._name = name
        if not hasattr(self, "_description"):
            self._description = description

    @property
    def name(self) -> str:
        return getattr(self, "_name", "")

    @property
    def description(self) -> str:
        return getattr(self, "_description", "")

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name!r})>"
