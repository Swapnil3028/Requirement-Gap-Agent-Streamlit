"""Shared data structures for requirement review agents."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Requirement:
    """A requirement statement extracted from an input document."""

    identifier: str
    source: str
    location: str
    text: str
    acceptance_criteria: str | None = None
    source_path: str | None = None


@dataclass(frozen=True)
class Finding:
    """An actionable issue raised during requirement review."""

    category: str
    severity: str
    requirement: Requirement | None
    reason: str
    recommendation: str
    related_requirement: Requirement | None = None
    clarification_question: str | None = None
    generated_scenarios: dict[str, tuple[str, ...]] | None = None
    document_scope: str | None = None
    supporting_requirements: tuple[Requirement, ...] = ()
    merged_categories: tuple[str, ...] = ()
    confidence_score: int = 80
