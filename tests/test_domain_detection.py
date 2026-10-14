"""Tests for full-document business-domain detection and QA guidance."""

import unittest

from agents.common import Requirement
from agents.domain_detection import detect_business_domain
from agents.quality_score import assess_requirement_quality
from app import render_report, review_requirements


def requirement(identifier: str, text: str) -> Requirement:
    return Requirement(
        identifier=identifier,
        source="requirements.txt",
        location=f"paragraph {identifier}",
        text=text,
    )


class BusinessDomainDetectionTests(unittest.TestCase):
    def test_online_payment_document_is_classified_as_ecommerce(self) -> None:
        assessment = detect_business_domain(
            [
                requirement("1", "Customer can pay online."),
                requirement("2", "The order confirmation is displayed."),
            ]
        )

        self.assertEqual(assessment.domain, "E-commerce")
        self.assertGreater(assessment.confidence, 0)
        self.assertIn("Which payment methods", assessment.review_questions[0])

    def test_banking_context_provides_banking_specific_review_prompts(self) -> None:
        assessment = detect_business_domain(
            [
                requirement("1", "A customer can transfer funds from a bank account."),
                requirement("2", "Transaction limits apply to each account."),
            ]
        )

        self.assertEqual(assessment.domain, "Banking")
        self.assertTrue(
            any("fraud checks" in question for question in assessment.review_questions)
        )
        self.assertTrue(
            any("audit trail" in question for question in assessment.review_questions)
        )

    def test_requested_domains_are_detected(self) -> None:
        examples = {
            "Retail": "The point of sale updates store inventory.",
            "Insurance": "The policyholder submits an insurance claim.",
            "Healthcare": "A patient views a medical record.",
            "Travel": "A traveler changes a flight reservation.",
            "Manufacturing": "The factory inspects each production batch.",
        }
        for expected_domain, text in examples.items():
            with self.subTest(domain=expected_domain):
                assessment = detect_business_domain([requirement("1", text)])
                self.assertEqual(assessment.domain, expected_domain)

    def test_weak_or_tied_domain_evidence_is_reported_as_undetermined(self) -> None:
        assessment = detect_business_domain(
            [requirement("1", "The customer sees the account status.")]
        )

        self.assertEqual(assessment.domain, "Undetermined")
        self.assertEqual(assessment.confidence, 0)
        self.assertIn("Which business domain", assessment.review_questions[0])

    def test_tied_domain_evidence_is_not_reported_as_a_definite_domain(self) -> None:
        assessment = detect_business_domain(
            [requirement("1", "The product catalog is available.")]
        )

        self.assertEqual(assessment.domain, "Undetermined")
        self.assertEqual(
            set(assessment.alternative_domains),
            {"Retail", "E-commerce"},
        )

    def test_domain_prompts_do_not_create_findings_or_score_deductions(self) -> None:
        requirements = [
            requirement("1", "Customer can pay online."),
        ]

        findings = review_requirements(requirements)
        report = render_report(requirements, findings)

        self.assertIn("## Business Domain Context", report)
        self.assertIn("Likely domain:** E-commerce", report)
        self.assertIn("domain-informed review prompts, not findings", report)
        self.assertNotIn("Which payment methods", [
            finding.reason for finding in findings
        ])
        score = assess_requirement_quality(findings).score
        self.assertIn(f"Requirement quality score: **{score}/100**", report)


if __name__ == "__main__":
    unittest.main()
