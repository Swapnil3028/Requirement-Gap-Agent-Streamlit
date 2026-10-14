"""Detect requirements that lack observable or measurable outcomes."""

import re
from collections.abc import Sequence

from agents.acceptance_agent import has_acceptance_criteria
from agents.ambiguity_agent import _PATTERN as _VAGUE_PATTERN
from agents.common import Finding, Requirement


_OUTCOME = re.compile(
    r"\b(?:accepts?|blocks?|creates?|deletes?|denies?|displays?|logs?|rejects?|"
    r"redirects?|returns?|responds?|saves?|sends?|shows?|stores?|updates?)\b",
    re.IGNORECASE,
)
_MEASURE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:%|milliseconds?|ms|seconds?|minutes?|hours?|days?|"
    r"weeks?|months?|years?|items?|records?|users?|requests?)\b"
    r"|\b(?:at least|at most|no more than|no less than|less than|greater than|"
    r"within|between|equal to|maximum of|minimum of)\b",
    re.IGNORECASE,
)


def detect_testability_gaps(requirements: Sequence[Requirement]) -> list[Finding]:
    """Flag criteria-bearing statements without an observable or measurable test signal."""
    findings = []
    for requirement in requirements:
        if not has_acceptance_criteria(requirement):
            continue

        vague_terms = sorted(
            {
                match.group(0).lower()
                for match in _VAGUE_PATTERN.finditer(requirement.text)
            }
        )
        if vague_terms:
            continue

        has_outcome = bool(_OUTCOME.search(requirement.text))
        has_measure = bool(_MEASURE.search(requirement.text))
        if not (has_outcome or has_measure):
            reason = (
                "No observable result or measurable threshold is stated in the "
                f"uploaded requirement “{requirement.text}”."
            )
            findings.append(
                Finding(
                    category="Testability gap",
                    severity="Medium",
                    requirement=requirement,
                    reason=reason,
                    recommendation=(
                        f"For “{requirement.text}”, add the missing test oracle: "
                        "the observable result or measurable pass/fail threshold. "
                        "Do not infer an expected result that is absent from the source."
                    ),
                )
            )
    return findings
