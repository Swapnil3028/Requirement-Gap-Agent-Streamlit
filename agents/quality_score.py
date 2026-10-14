"""Requirement quality scoring based on the configured review categories."""

from collections.abc import Sequence
from dataclasses import dataclass

from agents.common import Finding

QUALITY_SCORE_MODEL = "requirement_quality_v1"


@dataclass(frozen=True)
class QualityAssessment:
    score: int
    readiness_status: str
    ambiguous_requirements: int
    missing_acceptance_criteria: int
    missing_business_rules: int
    missing_nfrs: int
    conflicting_rules: int


def readiness_status_for_score(score: int) -> str:
    """Map a bounded quality score to the agreed development-readiness band."""
    if not 0 <= score <= 100:
        raise ValueError("Requirement quality score must be between 0 and 100.")
    if score < 50:
        return "High Risk"
    if score < 85:
        return "Needs Clarification"
    return "Ready for Development"


def assess_requirement_quality(findings: Sequence[Finding]) -> QualityAssessment:
    """Apply the requested fixed deductions and readiness thresholds."""
    ambiguous = sum(f.category == "Ambiguous requirement" for f in findings)
    missing_ac = sum(f.category == "Missing acceptance criteria" for f in findings)
    missing_business_rules = sum(
        f.category.startswith("Missing business rule:") for f in findings
    )
    missing_nfrs = sum(
        f.category.startswith(("Missing NFR:", "Incomplete NFR:"))
        for f in findings
    )
    conflicts = sum(f.category == "Conflicting business rules" for f in findings)
    score = max(
        0,
        100
        - ambiguous * 10
        - missing_ac * 10
        - missing_business_rules * 5
        - missing_nfrs * 5
        - conflicts * 15,
    )
    return QualityAssessment(
        score=score,
        readiness_status=readiness_status_for_score(score),
        ambiguous_requirements=ambiguous,
        missing_acceptance_criteria=missing_ac,
        missing_business_rules=missing_business_rules,
        missing_nfrs=missing_nfrs,
        conflicting_rules=conflicts,
    )
