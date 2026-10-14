"""Detect missing acceptance criteria."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from agents.common import Finding, Requirement


_AC_LABEL_WITH_CONTENT = re.compile(
    r"\b(?:acceptance criteria|acceptance tests?|definition of done|done when)"
    r"\s*[:\-]\s*\S",
    re.IGNORECASE,
)
_GIVEN_WHEN_THEN = re.compile(r"\bgiven\b.+\bwhen\b.+\bthen\b", re.IGNORECASE | re.DOTALL)
_GWT_MARKERS = re.compile(r"\b(?:given|when|then)\s*:", re.IGNORECASE)
_USER_STORY_ACTION = re.compile(
    r"\bas\s+(?P<actor>[^,]+),?\s+i\s+(?:can|may|need to|want to)\s+"
    r"(?P<action>[^.!?]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AcceptanceCriteriaAnalysis:
    """AC availability and draft scenarios for a user story."""

    available: bool
    scenarios: dict[str, tuple[str, ...]]


def has_acceptance_criteria(requirement: Requirement) -> bool:
    """Return true only when acceptance-criteria evidence has non-empty content."""
    if requirement.acceptance_criteria and requirement.acceptance_criteria.strip():
        return True
    if _GIVEN_WHEN_THEN.search(requirement.text):
        return True
    if _AC_LABEL_WITH_CONTENT.search(requirement.text):
        return True
    return len(_GWT_MARKERS.findall(requirement.text)) >= 3


def generate_acceptance_scenarios(requirement: Requirement) -> dict[str, tuple[str, ...]]:
    """Draft only criteria whose actor and action are explicit in the source."""
    story = requirement.text
    story_action = _USER_STORY_ACTION.search(story)
    if story_action is None:
        return {}

    actor = story_action.group("actor").strip()
    action = story_action.group("action").strip()
    return {
        "Positive scenarios": (
            f"Given {actor} and [the success precondition to be defined by the "
            f"Product Owner], When they {action}, Then [the expected success "
            "outcome is not stated in the uploaded story; the story does not "
            "state that outcome].",
        ),
        "Negative scenarios": (
            f"Given [the disallowed condition to be defined by the Product Owner] "
            f"for “{story}”, When {actor} attempts to {action}, Then [the expected "
            "rejection or handling is not stated in the uploaded story].",
        ),
        "Validation scenarios": (
            f"Given [the input or validation rule to be defined by the Product "
            f"Owner] for “{story}”, When {actor} attempts to {action}, Then [the "
            "validation result is not stated in the uploaded story].",
        ),
        "Exception scenarios": (
            f"Given [the applicable exception or failure condition to be defined "
            f"by the Product Owner] for “{story}”, When {actor} attempts to "
            f"{action}, Then [the exception handling is not stated in the "
            "uploaded story].",
        ),
        "Boundary scenarios": (
            f"Given [the applicable boundary values or state to be defined by the "
            f"Product Owner] for “{story}”, When {actor} attempts to {action}, "
            "Then [the boundary behavior is not stated in the uploaded story].",
        ),
    }


def analyze_acceptance_criteria(
    requirement: Requirement,
) -> AcceptanceCriteriaAnalysis:
    """Check for existing AC and draft scenarios only when criteria are missing."""
    available = has_acceptance_criteria(requirement)
    return AcceptanceCriteriaAnalysis(
        available=available,
        scenarios={} if available else generate_acceptance_scenarios(requirement),
    )


def detect_missing_acceptance_criteria(
    requirements: Sequence[Requirement],
) -> list[Finding]:
    """Flag statements without explicit acceptance-criteria evidence."""
    findings = []
    for requirement in requirements:
        analysis = analyze_acceptance_criteria(requirement)
        if not analysis.available:
            findings.append(
                Finding(
                    category="Missing acceptance criteria",
                    severity="High",
                    requirement=requirement,
                    reason=(
                        "No non-empty acceptance criteria or Given/When/Then "
                        f"scenario was found for the uploaded requirement “{requirement.text}”."
                    ),
                    recommendation=(
                        f"Add testable Given/When/Then criteria for “{requirement.text}”. "
                        "The expected outcome is not stated in the uploaded text and "
                        "must be supplied by the requirement owner."
                    ),
                    generated_scenarios=analysis.scenarios,
                )
            )
    return findings