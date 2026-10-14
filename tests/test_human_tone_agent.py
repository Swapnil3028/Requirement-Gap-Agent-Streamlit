"""Tests for the final stakeholder-facing finding rewrite stage."""

import unittest

from agents.common import Finding, Requirement
from agents.human_tone_agent import HumanToneRewriteAgent
from app import render_report


class HumanToneRewriteAgentTests(unittest.TestCase):
    def test_rewrite_adds_rationale_and_business_impact_without_mutating_finding(self) -> None:
        requirement = Requirement(
            identifier="REQ-1",
            source="BRD.docx",
            location="paragraph 4",
            text="The system responds quickly.",
        )
        finding = Finding(
            category="Ambiguous requirement",
            severity="High",
            requirement=requirement,
            reason="The word quickly has no measurable threshold.",
            recommendation="Define a measurable response-time target.",
            confidence_score=92,
        )
        original = (
            finding.category,
            finding.severity,
            finding.confidence_score,
            finding.recommendation,
        )

        rewritten = HumanToneRewriteAgent().rewrite(finding)

        self.assertIs(rewritten.finding, finding)
        self.assertEqual(rewritten.title, "The requirement needs a clearer definition")
        self.assertIn(requirement.text, rewritten.rationale)
        self.assertIn("Different teams", rewritten.business_impact)
        self.assertEqual(
            rewritten.recommendation,
            "Define a measurable response-time target.",
        )
        self.assertEqual(
            (
                finding.category,
                finding.severity,
                finding.confidence_score,
                finding.recommendation,
            ),
            original,
        )

    def test_report_presents_rationale_impact_and_action_after_technical_review(self) -> None:
        finding = Finding(
            category="Missing business rule: calculation",
            severity="High",
            requirement=None,
            reason="The calculation basis is not stated.",
            recommendation="Specify the inputs, formula, and rounding rules.",
        )

        report = render_report([], [finding])

        self.assertIn("- **Rationale:**", report)
        self.assertIn("- **Business impact:**", report)
        self.assertIn("- **Recommendation:**", report)
        self.assertIn("Different assumptions about inputs or rounding", report)
        self.assertIn("Specify the inputs, formula, and rounding rules.", report)

    def test_rewrite_all_preserves_finding_order(self) -> None:
        findings = [
            Finding(
                category="Missing acceptance criteria",
                severity="High",
                requirement=None,
                reason="No criteria were identified.",
                recommendation="Add criteria.",
            ),
            Finding(
                category="Missing NFR: Security",
                severity="Medium",
                requirement=None,
                reason="No security target was identified.",
                recommendation="Confirm a security target.",
            ),
        ]

        rewritten = HumanToneRewriteAgent().rewrite_all(findings)

        self.assertEqual([item.finding for item in rewritten], findings)
        self.assertIn("complete", rewritten[0].business_impact)
        self.assertIn("security requirements", rewritten[1].business_impact)


if __name__ == "__main__":
    unittest.main()
