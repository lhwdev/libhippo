"""Knowledge node data structures and YAML frontmatter schemas."""

from __future__ import annotations

import re
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Namespace = Literal["common", "user", "project", "plugins"]
NodeStatus = Literal["active", "deprecated", "needs_review"]
NodeNature = Literal["foundation", "critical_rule", "transient_tip"]


class KnowledgeFrontmatter(BaseModel):
    """Frontmatter metadata schema for knowledge markdown nodes."""

    title: str
    namespace: Namespace
    version: str = "1.0.0"
    status: NodeStatus = "active"
    nature: NodeNature = "foundation"
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    last_updated: str | None = None
    last_accessed: str | None = None
    access_count: int = 0
    tags: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)


class KnowledgeCandidate(BaseModel):
    """Candidate knowledge document proposed for checking or commit."""

    path: str
    markdown: str
    frontmatter: KnowledgeFrontmatter | None = None
    body: str = ""
    parse_errors: list[str] = Field(default_factory=list)

    @classmethod
    def from_markdown(cls, path: str, markdown: str) -> KnowledgeCandidate:
        """Parse markdown containing YAML frontmatter."""
        pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
        match = re.match(pattern, markdown, re.DOTALL)
        if not match:
            return cls(
                path=path,
                markdown=markdown,
                body=markdown,
                parse_errors=["Missing or malformed YAML frontmatter delimiters ('---')"],
            )

        yaml_text, body = match.group(1), match.group(2)
        try:
            parsed_yaml = yaml.safe_load(yaml_text) or {}
            if not isinstance(parsed_yaml, dict):
                return cls(
                    path=path,
                    markdown=markdown,
                    body=body,
                    parse_errors=["Frontmatter YAML is not a key-value mapping"],
                )
            frontmatter = KnowledgeFrontmatter.model_validate(parsed_yaml)
            return cls(
                path=path,
                markdown=markdown,
                frontmatter=frontmatter,
                body=body.strip(),
            )
        except Exception as e:  # noqa: BLE001
            return cls(
                path=path,
                markdown=markdown,
                body=body.strip(),
                parse_errors=[f"Frontmatter schema validation error: {e}"],
            )


class HubReference(BaseModel):
    """Reference metadata for a parent knowledge."""

    path: str
    title: str = ""
    coarseness: int = 0


class SiblingReference(BaseModel):
    """Reference metadata for an existing sibling document in the same scope."""

    path: str
    title: str = ""
    summary: str = ""


class KnowledgeContext(BaseModel):
    """Surrounding taxonomy and repository context for evaluation."""

    parent: HubReference | None = None
    siblings: list[SiblingReference] = Field(default_factory=list)
    allowed_namespaces: list[Namespace] = Field(
        default_factory=lambda: ["common", "user", "project", "plugins"]
    )
