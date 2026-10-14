"""Tests for the requirement review checks."""

import unittest

from agents.acceptance_agent import (
    analyze_acceptance_criteria,
    detect_missing_acceptance_criteria,
)
from agents.ambiguity_agent import detect_ambiguity
from agents.clarification_agent import (
    PRIORITY_ORDER,
    generate_clarification_question,
    generate_clarification_questions,
)
from agents.common import Finding, Requirement
from agents.conflict_agent import detect_conflicts
from agents.deduplication_agent import DeduplicationAgent
from agents.testability_agent import detect_testability_gaps


def requirement(
    identifier: str, text: str, acceptance_criteria: str | None = None
) -> Requirement:
    return Requirement(
        identifier=identifier,
        source="requirements.txt",
        location=f"line {identifier}",
        text=text,
        acceptance_criteria=acceptance_criteria,
    )


class RequirementAgentTests(unittest.TestCase):
    def test_ambiguity_reports_vague_terms_once(self) -> None:
        item = requirement("1", "The application should be fast and easy to use.")

        findings = detect_ambiguity([item])

        self.assertEqual(len(findings), 1)
        self.assertIn("fast", findings[0].reason)
        self.assertIn("easy to use", findings[0].reason)

    def test_ambiguity_question_quotes_source_without_invented_thresholds(self) -> None:
        item = requirement("1", "System should load quickly.")

        findings = detect_ambiguity([item])

        self.assertEqual(len(findings), 1)
        question = generate_clarification_question(findings[0])
        self.assertIn(item.text, question)
        self.assertIn("quickly", question)
        self.assertNotIn("seconds", question)

    def test_ambiguity_recommendation_quotes_requirement(self) -> None:
        item = requirement("1", "Customer should see relevant products.")

        findings = detect_ambiguity([item])

        self.assertEqual(len(findings), 1)
        self.assertIn(item.text, findings[0].recommendation)
        self.assertIn("relevant", findings[0].recommendation)

    def test_acceptance_criteria_requires_non_empty_evidence(self) -> None:
        missing = requirement("1", "The system shall save each approved order.")
        present = requirement("2", "As a buyer, I can checkout.", "Order is saved.")

        findings = detect_missing_acceptance_criteria([missing, present])

        self.assertEqual([finding.requirement.identifier for finding in findings], ["1"])

    def test_missing_order_cancellation_ac_does_not_invent_business_rules(self) -> None:
        story = requirement("1", "As a customer, I can cancel orders.")

        analysis = analyze_acceptance_criteria(story)

        self.assertFalse(analysis.available)
        self.assertEqual(
            set(analysis.scenarios),
            {
                "Positive scenarios",
                "Negative scenarios",
                "Validation scenarios",
                "Exception scenarios",
                "Boundary scenarios",
            },
        )
        scenario = analysis.scenarios["Positive scenarios"][0]
        self.assertIn("a customer", scenario)
        self.assertIn("cancel orders", scenario)
        self.assertIn("does not state that outcome", scenario)
        for scenarios in analysis.scenarios.values():
            self.assertIn("[", scenarios[0])
            self.assertIn("Product Owner", scenarios[0])
        for unsupported in ("shipped", "refund", "partial", "not allowed"):
            self.assertNotIn(unsupported, " ".join(
                scenario_text
                for scenarios in analysis.scenarios.values()
                for scenario_text in scenarios
            ))

    def test_existing_acceptance_criteria_does_not_generate_scenarios(self) -> None:
        story = requirement(
            "1",
            "As a customer, I can cancel orders.",
            "Given an eligible order, when cancelled, then confirmation is shown.",
        )

        analysis = analyze_acceptance_criteria(story)

        self.assertTrue(analysis.available)
        self.assertEqual(analysis.scenarios, {})

    def test_given_when_then_is_recognized(self) -> None:
        item = requirement(
            "1",
            "Given a valid user, When they sign in, Then the dashboard is displayed.",
        )

        self.assertEqual(detect_missing_acceptance_criteria([item]), [])

    def test_conflict_detects_explicit_negation(self) -> None:
        allowed = requirement("1", "The system must allow guests to checkout.")
        denied = requirement("2", "The system must not allow guests to checkout.")

        findings = detect_conflicts([allowed, denied])

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].related_requirement, denied)

    def test_conflict_detects_opposite_state(self) -> None:
        enabled = requirement("1", "The account is enabled.")
        disabled = requirement("2", "The account is disabled.")

        self.assertEqual(len(detect_conflicts([enabled, disabled])), 1)

    def test_conflict_detects_age_threshold_mismatch(self) -> None:
        brd_rule = requirement("1", "Minimum age = 18.")
        registration_rule = requirement("2", "Users above 16 can register.")

        findings = detect_conflicts([brd_rule, registration_rule])

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "Medium")
        self.assertIn("Inconsistent thresholds", findings[0].reason)
        self.assertEqual(findings[0].related_requirement, registration_rule)

    def test_conflict_detects_different_required_ages_as_high(self) -> None:
        first = requirement("1", "Age must be 18.")
        second = requirement("2", "Age must be 16.")

        findings = detect_conflicts([first, second])

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "High")
        self.assertIn("cannot both be true", findings[0].reason)
        self.assertEqual(findings[0].related_requirement, second)

    def test_conflict_detects_disjoint_numeric_conditions_as_high(self) -> None:
        first = requirement("1", "Users must be at least 18 years old.")
        second = requirement("2", "Users must be under 18 years old.")

        findings = detect_conflicts([first, second])

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "High")
        self.assertIn("cannot both be true", findings[0].reason)

    def test_duplicate_rule_is_not_reported_as_a_conflict(self) -> None:
        first = requirement("1", "The system must display an order receipt.")
        second = requirement("2", "The system shall display an order receipt!")

        self.assertEqual(detect_conflicts([first, second]), [])

    def test_unrelated_rules_are_not_reported_as_conflicts(self) -> None:
        first = requirement("1", "The system must allow guests to checkout.")
        second = requirement("2", "The system must allow members to checkout.")

        self.assertEqual(detect_conflicts([first, second]), [])

    def test_testability_omits_ambiguous_duplicate_and_flags_unobservable_rule(self) -> None:
        observable = requirement("1", "The system displays an order confirmation.")
        ambiguous = requirement("2", "The system should be user-friendly.")
        unobservable = requirement(
            "3",
            "The customer account is ready.",
            "The customer account is ready.",
        )

        findings = detect_testability_gaps([observable, ambiguous, unobservable])

        self.assertEqual([finding.requirement.identifier for finding in findings], ["3"])
        self.assertIn(unobservable.text, findings[0].reason)

    def test_clarification_question_matches_finding_category(self) -> None:
        finding = detect_ambiguity([requirement("1", "The system responds soon.")])[0]

        self.assertIn(finding.requirement.text, generate_clarification_question(finding))

    def test_clarification_question_priority_uses_finding_severity(self) -> None:
        finding = detect_ambiguity(
            [requirement("1", "Checkout should complete quickly.")]
        )[0]

        questions = generate_clarification_questions([finding])

        self.assertEqual(tuple(questions), PRIORITY_ORDER)
        self.assertFalse(questions["Critical"])
        self.assertEqual(len(questions["Medium"]), 1)
        self.assertIn("Checkout should complete quickly.", questions["Medium"][0])

    def test_order_cancellation_question_does_not_assume_partial_cancel_or_refund(self) -> None:
        finding = detect_missing_acceptance_criteria(
            [requirement("1", "As a customer, I can cancel orders.")]
        )[0]

        questions = generate_clarification_questions([finding])

        self.assertEqual(len(questions["High"]), 1)
        self.assertIn("As a customer, I can cancel orders.", questions["High"][0])
        self.assertNotIn("partial", questions["High"][0])
        self.assertNotIn("refund", questions["High"][0])

    def test_missing_ac_questions_are_grouped_per_document_with_all_stories_cited(self) -> None:
        stories = [
            requirement("1", "As a customer, I can cancel an order."),
            requirement("2", "As a customer, I can view an invoice."),
            requirement("3", "As an admin, I can export a report."),
        ]

        findings = detect_missing_acceptance_criteria(stories)
        questions = generate_clarification_questions(findings)

        self.assertEqual(len(questions["High"]), 1)
        question = questions["High"][0]
        for story in stories:
            self.assertIn(story.text, question)
            self.assertIn(story.location, question)
        self.assertEqual(question.count("which acceptance-test scenarios"), 1)

    def test_deduplication_keeps_strongest_and_preserves_other_sources(self) -> None:
        first = requirement("1", "The system shall save approved orders.")
        duplicate = Requirement(
            identifier="2",
            source="requirements.txt",
            location="line 2",
            text=first.text,
        )
        weaker = Finding(
            category="Missing success criteria",
            severity="Medium",
            requirement=first,
            reason="No success outcome.",
            recommendation="Add an outcome.",
        )
        stronger = Finding(
            category="Missing acceptance criteria",
            severity="High",
            requirement=duplicate,
            reason="No acceptance criteria.",
            recommendation="Add acceptance criteria.",
        )

        result = DeduplicationAgent().consolidate([weaker, stronger])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].severity, "High")
        self.assertEqual(result[0].category, "Missing acceptance criteria")
        self.assertIn("Missing success criteria", result[0].merged_categories)
        self.assertIn(first, result[0].supporting_requirements)


if __name__ == "__main__":
    unittest.main()
