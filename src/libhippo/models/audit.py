"""TypeSafe Jev evaluation models and audit report contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TaxonomyFit = Literal["optimal", "misplaced", "rename_suggested"]
SizeStatus = Literal["optimal", "oversized", "undersized", "in_deadband"]
MergeRecommendation = Literal["none", "merge_into_sibling", "fold_into_parent_hub"]
RedundancyStatus = Literal["novel", "partial_overlap", "duplicate_conflict"]
AuditVerdict = Literal["PASS", "REVISE_SCHEMA", "MERGE_REQUIRED", "ESCALATE_REFACTOR"]


class JevAuditReport(BaseModel):
    """Rigid typed audit report produced by CheckerAgent (TypeSafe Jev model)."""

    # 1. Taxonomic Fit
    taxonomy_fit: TaxonomyFit = "optimal"
    suggested_path: str | None = None

    # 2. Local Deterministic Token Count + Jev Semantic Bloatiness
    raw_token_count: int = 0
    bloatiness_score: float = 1.0  # 0.0 ~ 3.0 spectrum (sparse stub -> lean -> discursive -> monolithic)
    effective_token_size: float = 0.0
    size_status: SizeStatus = "in_deadband"

    # 3. Importance Scoring (0.0 to 1.0)
    importance_score: float = Field(default=0.5, ge=0.0, le=1.0)

    # 4. Sibling Coalescence
    merge_candidate_siblings: list[str] = Field(default_factory=list)
    merge_recommendation: MergeRecommendation = "none"

    # 5. Coherence & Single Responsibility Principle (SRP)
    coherence_score: float = 1.0  # 0.0 to 1.0
    topic_drift_detected: bool = False

    # 6. Redundancy & Conflict Detection
    redundancy_status: RedundancyStatus = "novel"
    conflicting_paths: list[str] = Field(default_factory=list)

    # 7. Frontmatter Schema Compliance
    schema_valid: bool = True
    schema_errors: list[str] = Field(default_factory=list)

    # Overall Actionable Gate Verdict
    verdict: AuditVerdict = "PASS"
    details: dict[str, Any] = Field(default_factory=dict)
