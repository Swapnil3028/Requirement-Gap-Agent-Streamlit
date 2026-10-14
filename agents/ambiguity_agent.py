"""Detect vague or subjective wording in requirements."""

import re
from collections.abc import Sequence

from agents.common import Finding, Requirement


VAGUE_TERMS = (
    "as appropriate",
    "as needed",
    "as soon as possible",
    "easy to use",
    "etc",
    "fast",
    "flexible",
    "intuitive",
    "many",
    "minimal",
    "quick",
    "quickly",
    "reliable",
    "relevant",
    "robust",
    "seamless",
    "several",
    "soon",
    "sufficient",
    "timely",
    "user-friendly",
    "user friendly",
    "where possible",
)

_PATTERN = re.compile(
    r"\b(?:" + "|".join(re.escape(term) for term in VAGUE_TERMS) + r")\b",
    re.IGNORECASE,
)

def _clarification_question(requirement: Requirement, matches: set[str]) -> str:
    quoted_terms = ", ".join(f"“{term}”" for term in sorted(matches))
    return (
        f"In the uploaded requirement “{requirement.text}”, what measurable "
        f"threshold or observable rule defines {quoted_terms}?"
    )


def detect_ambiguity(requirements: Sequence[Requirement]) -> list[Finding]:
    """Flag statements containing one or more configured vague terms."""
    findings = []
    for requirement in requirements:
        matches = {match.group(0).lower() for match in _PATTERN.finditer(requirement.text)}
        if matches:
            findings.append(
                Finding(
                    category="Ambiguous requirement",
                    severity="Medium",
                    requirement=requirement,
                    reason=(
                        f"The uploaded requirement “{requirement.text}” contains "
                        f"subjective wording: {', '.join(sorted(matches))}."
                    ),
                    recommendation=(
                        f"Define {', '.join(f'“{term}”' for term in sorted(matches))} "
                        f"for “{requirement.text}” with a measurable threshold or "
                        "observable rule, if the intended meaning is not already "
                        "explicit in the uploaded text."
                    ),
                    clarification_question=_clarification_question(requirement, matches),
                )
            )
    return findings