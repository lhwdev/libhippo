"""Knowledge node data structures and YAML frontmatter schemas."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

Namespace = Literal["common", "user", "project", "plugins"]
NodeStatus = Literal["active", "deprecated", "needs_review"]
NodeNature = Literal["foundation", "critical_rule", "transient_tip"]


@dataclass
class KnowledgeAgentEvent:
    """Real-time observability event emitted during Maker-Checker curation/governance cycles."""

    agent: str  # "CuratorAgent" | "CheckerAgent" | "VerifierAgent" | "BookKeeperAgent" | "MakerChecker"
    action: str  # "drafting" | "audit" | "audit_verdict" | "refactor" | "mutation_executed" | "commit" | "merge"
    status: str  # "running" | "completed" | "passed" | "rejected" | "escalated" | "failed"
    target_path: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    input_summary: str = ""
    output_summary: str = ""
    timestamp: str = ""
    type: str = "knowledge_agent"



class KnowledgeFrontmatter(BaseModel):
    """Frontmatter metadata schema for knowledge markdown nodes."""

    # 1. Written by Agent
    title: str
    version: str = "1.0.0"
    source: list[str] = Field(default_factory=list)
    version_check: str | None = None
    tags: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)

    # 2. Classified by CheckerAgent / VerifierAgent
    status: NodeStatus = "active"
    importance: float = Field(default=0.5, ge=0.0, le=1.0)

    # 3. Systematically Resolved (derived from path/disk, omitted from standard frontmatter)
    namespace: Namespace | str | None = None
    nature: str | None = None
    last_updated: str | None = None
    last_accessed: str | None = None
    access_count: int = 0

    # 4. User-Only (reject agent input; only set manually by user)
    force_keep: bool = False


class KnowledgeCandidate(BaseModel):
    """Candidate knowledge document proposed for checking or commit."""

    path: str
    markdown: str
    frontmatter: KnowledgeFrontmatter | None = None
    body: str = ""
    parse_errors: list[str] = Field(default_factory=list)

    @property
    def namespace(self) -> str:
        """Systematically resolved namespace derived from path or physical mount."""
        if self.frontmatter and self.frontmatter.namespace:
            return self.frontmatter.namespace
        parts = self.path.split("/")
        return parts[0] if len(parts) > 1 else "common"

    def to_markdown(self) -> str:
        """Serialize frontmatter and body back to markdown with '---' delimiters."""
        if not self.frontmatter:
            return self.body
        fm_dict = self.frontmatter.model_dump(exclude_none=True)
        # Omit systematically resolved namespace and deprecated nature
        fm_dict.pop("namespace", None)
        fm_dict.pop("nature", None)
        # Omit force_keep unless explicitly true (user-set)
        if not fm_dict.get("force_keep"):
            fm_dict.pop("force_keep", None)
        # Omit internal access counters from frontmatter text
        fm_dict.pop("access_count", None)
        fm_dict.pop("last_accessed", None)
        if fm_dict.get("importance") == 0.5:
            # Default unclassified importance doesn't need to clutter frontmatter
            pass

        yaml_str = yaml.safe_dump(fm_dict, sort_keys=False).strip()
        body_clean = self.body.strip()
        return f"---\n{yaml_str}\n---\n\n{body_clean}\n" if body_clean else f"---\n{yaml_str}\n---\n"

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

            # Systematically resolve namespace from path if not provided
            if "namespace" not in parsed_yaml and path:
                parsed_yaml["namespace"] = path.split("/")[0] if "/" in path else "common"

            # Silent type coercions
            if "source" in parsed_yaml and isinstance(parsed_yaml["source"], str):
                parsed_yaml["source"] = [parsed_yaml["source"]]
            if "tags" in parsed_yaml and isinstance(parsed_yaml["tags"], str):
                parsed_yaml["tags"] = [t.strip() for t in parsed_yaml["tags"].split(",") if t.strip()]
            if "related" in parsed_yaml and isinstance(parsed_yaml["related"], str):
                parsed_yaml["related"] = [r.strip() for r in parsed_yaml["related"].split(",") if r.strip()]

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


def normalize_frontmatter(
    candidate: KnowledgeCandidate,
    parent_version_check: str | None = None,
    is_category_hub: bool = False,
) -> tuple[KnowledgeCandidate, list[str]]:
    """Silently normalize and auto-fix frontmatter discrepancies in-place.

    Returns (normalized_candidate, list_of_fixes_applied).
    """
    if not candidate.frontmatter:
        return candidate, []

    fixes: list[str] = []
    fm = candidate.frontmatter

    # Rule 1: Strip version_check on broad category hubs
    if is_category_hub and fm.version_check:
        fm.version_check = None
        fixes.append("Silently removed version_check from category hub")

    # Rule 2: Silently drop redundant version_check on child if parent defines it
    if parent_version_check and fm.version_check:
        fm.version_check = None
        fixes.append(f"Silently dropped redundant child version_check (parent defines '{parent_version_check}')")

    if fixes:
        candidate.markdown = candidate.to_markdown()

    return candidate, fixes


def reconcile_candidate_frontmatter(
    candidate: KnowledgeCandidate,
    previous_candidate: KnowledgeCandidate | None = None,
    importance: float | None = None,
    status: NodeStatus | None = None,
) -> KnowledgeCandidate:
    """Silently drop agent-provided properties for categories 2-4 and replace with previous/actual values.

    Categories:
    1. Written by Agent (title, version, source, version_check, tags, related) -> preserved from candidate.
    2. Classified by Checker (importance, status) -> replaced with actual audit value or previous value.
    3. Systematically Resolved (namespace, nature, timestamps, counters) -> derived from path and storage.
    4. User-Only (force_keep) -> replaced with previous value from disk (or False if new).
    """
    if not candidate.frontmatter:
        return candidate

    fm = candidate.frontmatter

    # Category 4: User-only force_keep (replace with previous value if modifying existing)
    if previous_candidate and previous_candidate.frontmatter:
        fm.force_keep = previous_candidate.frontmatter.force_keep

    # Category 2: Checker-classified importance & status
    if importance is not None:
        fm.importance = importance
    elif previous_candidate and previous_candidate.frontmatter:
        fm.importance = previous_candidate.frontmatter.importance

    if status is not None:
        fm.status = status
    elif previous_candidate and previous_candidate.frontmatter:
        fm.status = previous_candidate.frontmatter.status

    # Category 3: Systematically resolved
    fm.namespace = candidate.namespace
    fm.nature = None
    if previous_candidate and previous_candidate.frontmatter:
        fm.access_count = previous_candidate.frontmatter.access_count
        fm.last_accessed = previous_candidate.frontmatter.last_accessed
        if previous_candidate.frontmatter.last_updated:
            fm.last_updated = previous_candidate.frontmatter.last_updated

    candidate.markdown = candidate.to_markdown()
    return candidate


def update_markdown_version(markdown: str, new_version: str) -> str:
    """Update or inject version in markdown YAML frontmatter while preserving formatting."""
    pattern = r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?(.*)$"
    match = re.match(pattern, markdown, re.DOTALL)
    if not match:
        return markdown
    yaml_text, body = match.group(1), match.group(2)

    if re.search(r"^[ \t]*version\s*:", yaml_text, flags=re.MULTILINE):
        new_yaml = re.sub(
            r'^([ \t]*version\s*:\s*)(["\']?[^#\n\r]+?["\']?)(\s*(?:#.*)?)$',
            rf'\g<1>"{new_version}"\g<3>',
            yaml_text,
            flags=re.MULTILINE,
        )
    else:
        clean_yaml = yaml_text.rstrip()
        new_yaml = f"{clean_yaml}\nversion: \"{new_version}\"\n"

    if not new_yaml.endswith("\n"):
        new_yaml += "\n"

    return f"---\n{new_yaml}---\n{body}"


class HubReference(BaseModel):
    """Reference metadata for a parent knowledge."""

    path: str
    title: str = ""
    coarseness: int = 0
    version_check: str | None = None


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
