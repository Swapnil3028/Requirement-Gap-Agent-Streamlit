"""Assess requirement quality characteristics with explicit heuristic limits."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from agents.common import Finding, Requirement


@dataclass(frozen=True)
class QualityCharacteristic:
    name: str
    status: str
    evidence: str


_PRIORITY = re.compile(
    r"\b(?:priority\s*[:=]?\s*(?:p[0-3]|critical|high|medium|low)|"
    r"must have|should have|could have|won't have|"
    r"critical|high priority|medium priority|low priority)\b",
    re.IGNORECASE,
)
_MULTIPLE_ACTIONS = re.compile(
    r"\b(?:create|creates|display|displays|save|saves|send|sends|"
    r"update|updates|delete|deletes|process|processes|validate|validates|"
    r"approve|approves|cancel|cancels|calculate|calculates)\b"
    r".{1,80}\b(?:and|or)\b.{1,80}\b"
    r"(?:create|creates|display|displays|save|saves|send|sends|"
    r"update|updates|delete|deletes|process|processes|validate|validates|"
    r"approve|approves|cancel|cancels|calculate|calculates)\b",
    re.IGNORECASE,
)
_RATIONALE = re.compile(
    r"\b(?:because|so that|in order to|business value|rationale|"
    r"objective|purpose)\b",
    re.IGNORECASE,
)


def detect_prioritization_gaps(requirements: Sequence[Requirement]) -> list[Finding]:
    """Report requirements that have no explicit priority designation."""
    return [
        Finding(
            category="Missing prioritization",
            severity="Low",
            requirement=requirement,
            reason=(
                f"No explicit priority was identified for “{requirement.text}”."
            ),
            recommendation=(
                "Assign an agreed priority (for example, MoSCoW or P0–P3) "
                "and record it with the requirement."
            ),
            clarification_question=(
                f"What is the agreed delivery priority for “{requirement.text}”?"
            ),
        )
        for requirement in requirements
        if not _PRIORITY.search(requirement.text)
    ]


def detect_modifiability_concerns(
    requirements: Sequence[Requirement],
) -> list[Finding]:
    """Flag statements that appear to combine several independently changeable actions."""
    return [
        Finding(
            category="Modifiability concern",
            severity="Low",
            requirement=requirement,
            reason=(
                f"“{requirement.text}” may combine multiple actions or outcomes, "
                "making the requirement harder to change independently."
            ),
            recommendation=(
                "Check whether the statement should be split into independently "
                "traceable, reviewable requirements."
            ),
        )
        for requirement in requirements
        if _MULTIPLE_ACTIONS.search(requirement.text)
    ]


def assess_quality_characteristics(
    requirements: Sequence[Requirement],
    findings: Sequence[Finding],
) -> list[QualityCharacteristic]:
    """Summarize all requested quality dimensions and state evidence limitations."""
    categories = {
        category
        for finding in findings
        for category in (finding.category, *finding.merged_categories)
    }

    def contains(prefix: str) -> bool:
        return any(category.startswith(prefix) for category in categories)

    priority_gaps = contains("Missing prioritization")
    modifiability_gaps = contains("Modifiability concern")
    traceable = bool(requirements) and all(
        requirement.identifier
        and requirement.source
        and requirement.location
        for requirement in requirements
    )
    rationale_coverage = bool(requirements) and all(
        _RATIONALE.search(requirement.text) for requirement in requirements
    )
    complete_gaps = any(
        contains(prefix)
        for prefix in (
            "Missing acceptance criteria",
            "Missing business rule:",
            "Missing validation",
            "Missing success criteria",
        )
    )
    testability_gaps = any(
        contains(prefix)
        for prefix in (
            "Missing acceptance criteria",
            "Missing success criteria",
            "Missing validation",
            "Testability gap",
        )
    )

    return [
        QualityCharacteristic(
            "Correct",
            "Manual review required",
            "Correctness needs validation against authoritative business sources.",
        ),
        QualityCharacteristic(
            "Complete",
            "Review needed" if complete_gaps else "No configured gaps found",
            "Checks acceptance criteria, business rules, validation, and success criteria.",
        ),
        QualityCharacteristic(
            "Consistent",
            "Review needed" if contains("Conflicting business rules") else "No conflicts detected",
            "Cross-section contradiction and duplicate-rule checks run across the full upload.",
        ),
        QualityCharacteristic(
            "Unambiguous",
            "Review needed" if contains("Ambiguous requirement") else "No configured ambiguity detected",
            "Checks configured vague terms and document-wide measurable evidence.",
        ),
        QualityCharacteristic(
            "Testable",
            "Review needed" if testability_gaps else "No configured gaps found",
            "Checks for acceptance, validation, success, and observable pass/fail evidence.",
        ),
        QualityCharacteristic(
            "Traceable",
            "Source-linked" if traceable else "Review needed",
            "Each extracted statement is linked to its source document and location.",
        ),
        QualityCharacteristic(
            "Feasible",
            "Manual review required",
            "Feasibility depends on architecture, resources, and delivery constraints.",
        ),
        QualityCharacteristic(
            "Necessary",
            "Rationale recorded" if rationale_coverage else "Rationale review needed",
            "Checks whether each statement explains its purpose or business value.",
        ),
        QualityCharacteristic(
            "Prioritized",
            "Review needed" if priority_gaps else "Priority evidence found",
            "Checks for an explicit priority designation on each requirement.",
        ),
        QualityCharacteristic(
            "Verifiable",
            "Review needed" if testability_gaps else "No configured gaps found",
            "Checks for observable outcomes or measurable verification criteria.",
        ),
        QualityCharacteristic(
            "Modifiable",
            "Review needed" if modifiability_gaps else "No compound statements detected",
            "Flags statements that appear to bundle multiple actions or outcomes.",
        ),
    ]
