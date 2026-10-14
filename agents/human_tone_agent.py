"""Rewrite technical findings for clear, stakeholder-facing review output."""

from collections.abc import Sequence
from dataclasses import dataclass

from agents.common import Finding
from agents.finding_presentation import finding_display_explanation, finding_display_title


@dataclass(frozen=True)
class HumanizedFinding:
    """Presentation-only text; the source finding remains unchanged."""

    finding: Finding
    title: str
    rationale: str
    business_impact: str
    recommendation: str


_BUSINESS_IMPACTS = {
    "Ambiguous requirement": (
        "Different teams may make different assumptions, leading to behavior "
        "that does not match the stakeholder's intended outcome."
    ),
    "Missing acceptance criteria": (
        "The team may disagree on when the work is complete, increasing the "
        "risk of rework or acceptance delays."
    ),
    "Conflicting business rules": (
        "Different parts of the product may apply different rules, producing "
        "inconsistent outcomes for users."
    ),
    "Testability gap": (
        "The team may be unable to demonstrate consistently that the expected "
        "behavior has been delivered."
    ),
    "Missing validation": (
        "Invalid inputs may be handled inconsistently, creating avoidable "
        "user confusion or operational rework."
    ),
    "Missing success criteria": (
        "Users and delivery teams may not share the same understanding of a "
        "successful outcome."
    ),
    "Missing prioritization": (
        "Planning decisions may not reflect the business's intended delivery "
        "order when scope or capacity is constrained."
    ),
    "Modifiability concern": (
        "Future changes may be harder to estimate, test, and trace if the "
        "combined behaviors evolve independently."
    ),
}


def _business_impact(finding: Finding) -> str:
    category = finding.category
    if category.startswith("Missing NFR:"):
        area = category.partition(":")[2].strip().lower()
        return (
            f"If {area} requirements apply, teams may deliver different levels "
            "of support without an agreed target."
        )
    if category.startswith("Incomplete NFR:"):
        area = category.partition(":")[2].strip().lower()
        return (
            f"Without an agreed {area} target, teams may interpret the expected "
            "level of support differently."
        )
    if category.startswith("Missing business rule:"):
        if category.endswith(": calculation"):
            return (
                "Different assumptions about inputs or rounding may produce "
                "different business results."
            )
        return (
            "Different assumptions about workflow states or decision points "
            "may lead to inconsistent processing."
        )
    return _BUSINESS_IMPACTS.get(
        category,
        "Leaving this point unresolved may lead to inconsistent implementation "
        "or testing.",
    )


class HumanToneRewriteAgent:
    """Create human-readable finding text without changing review decisions."""

    def rewrite(self, finding: Finding) -> HumanizedFinding:
        return HumanizedFinding(
            finding=finding,
            title=finding_display_title(finding),
            rationale=finding_display_explanation(finding),
            business_impact=_business_impact(finding),
            recommendation=finding.recommendation,
        )

    def rewrite_all(
        self,
        findings: Sequence[Finding],
    ) -> list[HumanizedFinding]:
        return [self.rewrite(finding) for finding in findings]
