"""Tests for input loading, reporting, and the CLI."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from openpyxl import Workbook
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app import (
    InputError,
    load_requirements,
    main,
    render_report,
    restrict_retrieved_sections,
    review_requirements,
    review_requirements_with_audit,
    _requirements_from_text,
)
from agents.common import Finding, Requirement
from agents.completeness_agent import detect_missing_nfrs
from document_parser import extract_text
from rag_store import RetrievedSection, requirement_key


class FakeRequirementRAG:
    def __init__(self, persist_directory: Path) -> None:
        self.persist_directory = persist_directory

    def index_requirements(self, requirements: list[Requirement]) -> int:
        return len(requirements)

    def retrieve_for_requirements(
        self, requirements: list[Requirement], k: int
    ) -> dict[str, list[RetrievedSection]]:
        return {}

    def close(self) -> None:
        pass


class RequirementAppTests(unittest.TestCase):
    def test_report_starts_with_prioritized_executive_summary(self) -> None:
        findings = [
            Finding(
                category="Incomplete NFR: Performance",
                severity="Medium",
                requirement=None,
                reason="A specific performance target is not stated.",
                recommendation="Confirm an appropriate performance target.",
                confidence_score=91,
            ),
            Finding(
                category="Missing business rule: calculation",
                severity="Critical",
                requirement=None,
                reason="The calculation basis is not specified.",
                recommendation="Define the calculation basis.",
                confidence_score=75,
            ),
            Finding(
                category="Missing NFR: Performance",
                severity="Low",
                requirement=None,
                reason="A performance target is not stated.",
                recommendation="Confirm whether a performance target applies.",
                confidence_score=80,
            ),
        ]

        report = render_report([], findings)

        self.assertTrue(report.startswith("## Executive Summary"))
        self.assertIn("1. Calculation rules", report)
        self.assertIn("2. Performance targets", report)
        self.assertEqual(report.count("Performance targets"), 1)
        self.assertLess(
            report.index("1. Calculation rules"),
            report.index("2. Performance targets"),
        )

    def test_executive_summary_reports_when_no_clarification_areas_are_found(self) -> None:
        report = render_report([], [])

        self.assertTrue(report.startswith("## Executive Summary"))
        self.assertIn(
            "The configured review checks did not identify actionable clarification areas.",
            report,
        )

    def test_loads_markdown_paragraphs_and_skips_headings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.md"
            source.write_text(
                "# Stories\n\nThe system displays a receipt.\n", encoding="utf-8"
            )

            requirements = load_requirements(source)

        self.assertEqual(len(requirements), 1)
        self.assertEqual(requirements[0].text, "The system displays a receipt.")
        self.assertEqual(requirements[0].location, "paragraph 1")

    def test_joins_wrapped_text_and_ignores_single_words_and_scenario_titles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.txt"
            source.write_text(
                "Functional Requirements\n\n"
                "Checkout\n\n"
                "Scenario: Successful checkout\n\n"
                "Given a buyer has valid items\n\n"
                "When the buyer places an order\n\n"
                "Then an order confirmation is displayed\n\n"
                "As a buyer, I can view order history.",
                encoding="utf-8",
            )

            requirements = load_requirements(source)

        self.assertEqual(
            [requirement.text for requirement in requirements],
            [
                "Given a buyer has valid items When the buyer places an order "
                "Then an order confirmation is displayed",
                "As a buyer, I can view order history.",
            ],
        )
        missing_ac = [
            finding
            for finding in review_requirements(requirements)
            if finding.category == "Missing acceptance criteria"
        ]
        self.assertEqual(len(missing_ac), 1)
        self.assertEqual(
            missing_ac[0].requirement.text,
            "As a buyer, I can view order history.",
        )

    def test_pdf_wrapped_lines_form_sentences_without_heading_noise(self) -> None:
        requirements = _requirements_from_text(
            "Overview\n"
            "The system shall provide a secure account\n"
            "for each customer.\n"
            "Successful Login\n"
            "As a buyer, I can view order history.",
            Path("requirements.pdf"),
        )

        self.assertEqual(
            [requirement.text for requirement in requirements],
            [
                "The system shall provide a secure account for each customer.",
                "As a buyer, I can view order history.",
            ],
        )

    def test_empty_input_has_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "empty.txt"
            source.touch()

            with self.assertRaisesRegex(InputError, "empty"):
                load_requirements(source)

    def test_unsupported_input_has_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.rtf"
            source.write_text("not a supported source", encoding="utf-8")

            with self.assertRaisesRegex(InputError, "Unsupported input format"):
                load_requirements(source)

    def test_loads_docx_paragraphs_and_table_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "brd.docx"
            document = Document()
            document.add_paragraph("The system displays a receipt.")
            table = document.add_table(rows=1, cols=2)
            table.cell(0, 0).text = "REQ-1"
            table.cell(0, 1).text = "The system saves approved orders."
            document.save(source)

            requirements = load_requirements(source)

        self.assertEqual(len(requirements), 2)
        self.assertEqual(requirements[0].location, "paragraph 1")
        self.assertEqual(requirements[1].location, "paragraph 2")
        self.assertIn("REQ-1 | The system saves approved orders.", requirements[1].text)

    def test_docx_paragraphs_are_preserved_for_requirement_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "brd.docx"
            document = Document()
            document.add_paragraph("Business Requirements")
            document.add_paragraph("As a buyer, I can place an order.")
            document.add_paragraph("Scenario: Successful order")
            document.add_paragraph("Given a buyer has valid items")
            document.add_paragraph("When the buyer places an order")
            document.add_paragraph("Then a confirmation is displayed")
            document.save(source)

            extracted = extract_text(source)
            requirements = load_requirements(source)

        self.assertEqual(
            [requirement.text for requirement in requirements],
            [
                "As a buyer, I can place an order.",
                "Given a buyer has valid items When the buyer places an order "
                "Then a confirmation is displayed",
            ],
        )
        self.assertIn("\n\n", extracted)

    def test_extracts_and_loads_pdf_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "requirements.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=300, height=100)
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            font_reference = writer._add_object(font)
            page[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): font_reference}
                    )
                }
            )
            content = DecodedStreamObject()
            content.set_data(
                b"BT /F1 12 Tf 20 50 Td (The system displays a receipt.) Tj ET"
            )
            page[NameObject("/Contents")] = writer._add_object(content)
            with source.open("wb") as output:
                writer.write(output)

            extracted = extract_text(file_path=source)
            requirements = load_requirements(source)

        self.assertEqual(extracted, "The system displays a receipt.")
        self.assertEqual(requirements[0].text, "The system displays a receipt.")

    def test_text_extraction_cleans_spacing_and_wrapped_hyphenation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "requirements.txt"
            source.write_text(
                "  The\u00a0system shall pro-\ncess requests.  \n\n",
                encoding="utf-8",
            )

            extracted = extract_text(source)

        self.assertEqual(extracted, "The system shall process requests.")

    def test_loads_xlsx_headers_and_flags_empty_acceptance_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "stories.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["ID", "User Story", "Acceptance Criteria"])
            sheet.append(["US-1", "As a buyer, I can place an order.", None])
            sheet.append(["US-2", "As a buyer, I can view an order.", "Order is shown."])
            workbook.save(source)

            requirements = load_requirements(source)

        self.assertEqual(len(requirements), 2)
        self.assertEqual(requirements[0].location, "sheet 'Sheet', row 2")
        self.assertIsNone(requirements[0].acceptance_criteria)
        self.assertEqual(requirements[1].acceptance_criteria, "Order is shown.")
        findings = review_requirements(requirements)
        missing = [
            finding.requirement.identifier
            for finding in findings
            if finding.category == "Missing acceptance criteria"
        ]
        self.assertEqual(missing, ["stories.xlsx:1"])

    def test_report_includes_all_sections_and_escapes_table_delimiters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "requirements.txt"
            source.write_text(
                "The system responds soon | when asked.", encoding="utf-8"
            )
            requirements = load_requirements(source)
        findings = review_requirements(requirements)

        report = render_report(requirements, findings)

        self.assertIn("## The requirement needs a clearer definition (1)", report)
        self.assertIn("## Acceptance criteria need to be defined (1)", report)
        self.assertIn("## The document contains rules that need alignment (0)", report)
        self.assertIn("## The expected result is difficult to verify (0)", report)
        self.assertIn(r"\|", report)
        self.assertIn("Clarifying question", report)
        self.assertIn(
            "parser -> business-domain detection -> ambiguity -> acceptance criteria "
            "-> business rules -> NFR -> "
            "clarification -> deduplication -> already-answered question review -> "
            "senior QA and confidence scoring -> human tone rewrite -> final report",
            report,
        )

    def test_report_shows_conflict_severity_and_both_rules(self) -> None:
        first = Requirement(
            identifier="BRD:1",
            source="BRD.docx",
            location="paragraph 1",
            text="Minimum age = 18.",
        )
        second = Requirement(
            identifier="BRD:2",
            source="BRD.docx",
            location="paragraph 2",
            text="Users above 16 can register.",
        )

        report = render_report([first, second], review_requirements([first, second]))

        self.assertIn("**Conflict severity:** Medium", report)
        self.assertIn("Minimum age = 18.", report)
        self.assertIn("Users above 16 can register.", report)
        self.assertIn("different numeric thresholds", report)

    def test_rag_context_from_documents_outside_batch_is_ignored(self) -> None:
        current = Requirement(
            identifier="BRD:1",
            source="BRD.docx",
            location="paragraph 1",
            text="Age must be 18.",
            source_path="C:\\requirements\\BRD.docx",
        )
        retrieved = Requirement(
            identifier=current.identifier,
            source="Policy.txt",
            location=current.location,
            text="Age must be 16.",
            source_path=current.source_path,
        )
        context = {
            requirement_key(current): [RetrievedSection(retrieved, chunk_index=0)]
        }

        findings = review_requirements([current], context)

        self.assertFalse(
            any(finding.category == "Conflicting business rules" for finding in findings)
        )
        self.assertEqual(restrict_retrieved_sections([current], context), {requirement_key(current): []})

    def test_report_lists_retrieved_sections(self) -> None:
        current = Requirement(
            identifier="BRD:1",
            source="BRD.docx",
            location="paragraph 1",
            text="Orders may be cancelled before shipment.",
        )
        related = Requirement(
            identifier="Policy:2",
            source="Policy.txt",
            location="line 2",
            text="Shipped orders cannot be cancelled.",
        )
        context = {
            requirement_key(current): [RetrievedSection(related, chunk_index=0)]
        }

        report = render_report([current, related], [], context)

        self.assertIn("## Retrieved Related Sections", report)
        self.assertIn("Shipped orders cannot be cancelled.", report)

    def test_report_groups_clarification_questions_by_priority(self) -> None:
        requirement = Requirement(
            identifier="US-1",
            source="stories.txt",
            location="line 1",
            text="Checkout should complete quickly.",
        )

        report = render_report(
            [requirement],
            review_requirements([requirement]),
        )

        self.assertIn("## Product Owner Clarification Questions", report)
        self.assertIn("### Critical", report)
        self.assertIn("### High", report)
        self.assertIn("### Medium", report)
        self.assertIn("### Low", report)
        self.assertIn("Checkout should complete quickly.", report)
        self.assertNotIn("2, 5, or 10 seconds", report)

    def test_report_scenarios_leave_missing_business_rules_unresolved(self) -> None:
        story = Requirement(
            identifier="US-1",
            source="stories.txt",
            location="line 1",
            text="As a customer, I can cancel orders.",
        )

        report = render_report([story], review_requirements([story]))

        self.assertIn("Draft scenarios", report)
        self.assertIn("Positive scenarios", report)
        self.assertIn("Negative scenarios", report)
        self.assertIn("Boundary scenarios", report)
        self.assertIn("Validation scenarios", report)
        self.assertIn("Exception scenarios", report)
        self.assertIn("does not state that outcome", report)
        self.assertNotIn("refund", report)
        self.assertNotIn("shipped", report)

    def test_review_reports_document_level_nfr_coverage_without_fake_requirement(self) -> None:
        requirement = Requirement(
            identifier="REQ-1",
            source="requirements.txt",
            location="line 1",
            text="The system displays a receipt.",
        )

        findings = review_requirements([requirement])
        report = render_report([requirement], findings)

        nfr_findings = [finding for finding in findings if finding.category.startswith("Missing NFR:")]
        self.assertEqual(len(nfr_findings), 12)
        self.assertTrue(all(finding.requirement is None for finding in nfr_findings))
        self.assertIn("No source requirement identified for this document-level gap.", report)
        self.assertIn("not evidence that the system lacks this quality attribute", report)
        self.assertIn("## Non-Functional Requirement Review", report)

    def test_report_includes_all_quality_characteristics_and_nfr_areas(self) -> None:
        requirement = Requirement(
            identifier="REQ-1",
            source="requirements.txt",
            location="paragraph 1",
            text="The system displays a receipt.",
        )
        report = render_report([requirement], review_requirements([requirement]))

        for characteristic in (
            "Correct",
            "Complete",
            "Consistent",
            "Unambiguous",
            "Testable",
            "Traceable",
            "Feasible",
            "Necessary",
            "Prioritized",
            "Verifiable",
            "Modifiable",
        ):
            self.assertIn(f"| {characteristic} |", report)
        for area in (
            "Performance",
            "Security",
            "Authorization",
            "Authentication",
            "Audit",
            "Logging",
            "Availability",
            "Scalability",
            "Accessibility",
            "Compliance",
            "Localization",
            "Recovery",
        ):
            self.assertIn(f"Missing NFR: {area}", report)

    def test_review_flags_missing_validation_and_success_criteria(self) -> None:
        requirement = Requirement(
            identifier="REQ-1",
            source="requirements.txt",
            location="paragraph 1",
            text="The system validates the order input and processes the order.",
        )

        findings = review_requirements([requirement])
        categories = {
            category
            for finding in findings
            for category in (finding.category, *finding.merged_categories)
        }

        self.assertIn("Missing validation", categories)
        self.assertIn("Missing success criteria", categories)
        success_finding = next(
            finding
            for finding in findings
            if finding.category == "Missing acceptance criteria"
        )
        self.assertIn("Missing success criteria", success_finding.merged_categories)

    def test_conflicts_are_checked_across_document_sources_and_sections(self) -> None:
        brd = Requirement(
            identifier="BRD-1",
            source="BRD.docx",
            location="Business Rules, paragraph 4",
            text="The system must allow guests to checkout.",
        )
        appendix = Requirement(
            identifier="APP-1",
            source="Appendix.docx",
            location="Appendix A, paragraph 2",
            text="The system must not allow guests to checkout.",
        )

        findings = review_requirements([brd, appendix])

        self.assertTrue(
            any(
                finding.category == "Conflicting business rules"
                and finding.requirement == brd
                and finding.related_requirement == appendix
                for finding in findings
            )
        )

    def test_pipeline_runs_final_review_stages_in_requested_order(self) -> None:
        stages = []

        class Stage:
            def __init__(self, name):
                self.name = name

            def review(self, findings, requirements=None):
                stages.append(self.name)
                if self.name == "already answered":
                    return list(findings), []
                return list(findings)

            def consolidate(self, findings):
                stages.append(self.name)
                return list(findings)

        requirement = Requirement(
            identifier="REQ-1",
            source="requirements.txt",
            location="paragraph 1",
            text="The system displays a receipt.",
        )
        with (
            patch("app.ClarificationAgent", return_value=Stage("clarification")),
            patch("app.DeduplicationAgent", return_value=Stage("deduplication")),
            patch(
                "app.WasThisAlreadyAnsweredAgent",
                return_value=Stage("already answered"),
            ),
            patch("app.SeniorQAReviewAgent", return_value=Stage("senior QA")),
        ):
            review_requirements([requirement])

        self.assertEqual(
            stages,
            ["clarification", "deduplication", "already answered", "senior QA"],
        )

    def test_deep_nfr_checks_accept_area_specific_controls(self) -> None:
        detailed_requirements = [
            "Performance response time is 2 seconds.",
            "Data is encrypted using TLS.",
            "Access is role-based with least privilege.",
            "Multi-factor authentication is required.",
            "The audit trail records who did what.",
            "Logs are retained for 90 days.",
            "Availability target is 99.9% uptime.",
            "Supports 1,000 concurrent users.",
            "The interface complies with WCAG 2.1 AA.",
            "The product complies with GDPR.",
            "Supported languages: English and French.",
            "RTO is 4 hours and RPO is 15 minutes.",
        ]
        requirements = [
            Requirement(
                identifier=f"REQ-{index}",
                source="requirements.txt",
                location=f"paragraph {index}",
                text=text,
            )
            for index, text in enumerate(detailed_requirements, 1)
        ]

        findings = detect_missing_nfrs(requirements)

        self.assertEqual(findings, [])

    def test_deep_nfr_check_flags_mentioned_but_unmeasured_area(self) -> None:
        requirement = Requirement(
            identifier="REQ-1",
            source="requirements.txt",
            location="paragraph 1",
            text="The system should be available.",
        )

        findings = detect_missing_nfrs([requirement])

        self.assertIn(
            "Incomplete NFR: Availability",
            [finding.category for finding in findings],
        )

    def test_review_detects_unspecified_calculation_and_workflow_rules(self) -> None:
        requirements = [
            Requirement(
                identifier="REQ-1",
                source="requirements.txt",
                location="line 1",
                text="The system calculates the total.",
            ),
            Requirement(
                identifier="REQ-2",
                source="requirements.txt",
                location="line 2",
                text="The system approves a request.",
            ),
        ]

        findings = review_requirements(requirements)
        business_rule_findings = [
            finding
            for finding in findings
            if finding.category.startswith("Missing business rule:")
        ]

        self.assertEqual(
            {finding.category for finding in business_rule_findings},
            {
                "Missing business rule: calculation",
                "Missing business rule: workflow",
            },
        )
        self.assertTrue(all(finding.requirement is not None for finding in business_rule_findings))

    def test_document_reconciliation_uses_related_acceptance_evidence(self) -> None:
        story = Requirement(
            identifier="US-1",
            source="stories.txt",
            location="paragraph 1",
            text="As a buyer, I can place an order.",
        )
        scenario = Requirement(
            identifier="US-2",
            source="stories.txt",
            location="paragraph 2",
            text=(
                "Given a buyer has a valid order, When the buyer submits it, "
                "Then an order confirmation is displayed."
            ),
        )

        findings = review_requirements([story, scenario])

        self.assertFalse(
            any(
                finding.category == "Missing acceptance criteria"
                and finding.requirement == story
                for finding in findings
            )
        )

    def test_document_reconciliation_does_not_use_unrelated_document_evidence(self) -> None:
        checkout = Requirement(
            identifier="REQ-1",
            source="requirements.txt",
            location="paragraph 1",
            text="Checkout should complete quickly.",
        )
        reporting = Requirement(
            identifier="REQ-2",
            source="requirements.txt",
            location="paragraph 2",
            text="Reports must be generated within 2 seconds.",
        )

        findings = review_requirements([checkout, reporting])

        self.assertTrue(
            any(
                finding.category == "Ambiguous requirement"
                and finding.requirement == checkout
                for finding in findings
            )
        )

    def test_document_reconciliation_answers_ambiguity_from_related_threshold(self) -> None:
        vague = Requirement(
            identifier="REQ-1",
            source="requirements.txt",
            location="paragraph 1",
            text="Checkout should complete quickly.",
        )
        threshold = Requirement(
            identifier="REQ-2",
            source="requirements.txt",
            location="paragraph 2",
            text="Checkout must finish within 2 seconds.",
        )

        findings = review_requirements([vague, threshold])

        self.assertFalse(
            any(
                finding.category == "Ambiguous requirement"
                and finding.requirement == vague
                for finding in findings
            )
        )

    def test_document_reconciliation_answers_ambiguity_from_same_requirement(self) -> None:
        requirement = Requirement(
            identifier="REQ-1",
            source="requirements.txt",
            location="paragraph 1",
            text="Checkout should complete quickly, within 2 seconds.",
        )

        findings = review_requirements([requirement])

        self.assertFalse(
            any(
                finding.category == "Ambiguous requirement"
                and finding.requirement == requirement
                for finding in findings
            )
        )

    def test_already_answered_agent_removes_question_and_records_answer_evidence(self) -> None:
        vague = Requirement(
            identifier="REQ-1",
            source="BRD.docx",
            location="paragraph 1",
            text="The system accepts an upload as appropriate.",
        )
        answer = Requirement(
            identifier="REQ-2",
            source="BRD.docx",
            location="Upload Constraints, paragraph 3",
            text="Maximum upload size: 20 MB.",
        )

        findings, answered_questions = review_requirements_with_audit([vague, answer])

        self.assertFalse(
            any(
                finding.category == "Ambiguous requirement"
                and finding.requirement == vague
                for finding in findings
            )
        )
        self.assertEqual(len(answered_questions), 1)
        self.assertIn(vague.text, answered_questions[0].question)
        self.assertEqual(answered_questions[0].answer, answer.text)
        self.assertEqual(answered_questions[0].source, answer.source)
        self.assertEqual(answered_questions[0].location, answer.location)
        self.assertEqual(answered_questions[0].reason, "Answer already available.")
        report = render_report(
            [vague, answer],
            findings,
            answered_questions=answered_questions,
        )
        self.assertIn("## Questions Already Answered in the Document", report)
        self.assertIn("**Question removed:**", report)
        self.assertIn("Maximum upload size: 20 MB.", report)
        self.assertIn("**Reason:** Answer already available.", report)

    def test_document_reconciliation_answers_calculation_question_from_source(self) -> None:
        calculation = Requirement(
            identifier="REQ-1",
            source="requirements.txt",
            location="paragraph 1",
            text="The system calculates the order total.",
        )
        rule = Requirement(
            identifier="REQ-2",
            source="requirements.txt",
            location="paragraph 2",
            text="The order total is calculated as the sum of item prices.",
        )

        findings = review_requirements([calculation, rule])

        self.assertFalse(
            any(
                finding.category == "Missing business rule: calculation"
                and finding.requirement == calculation
                for finding in findings
            )
        )
    def test_report_contains_weighted_score_and_readiness(self) -> None:
        requirement = Requirement(
            identifier="REQ-1",
            source="requirements.txt",
            location="line 1",
            text="The system responds quickly.",
        )

        report = render_report([requirement], review_requirements([requirement]))

        self.assertIn("## Overall Readiness and Quality Score", report)
        self.assertIn("Score: **20/100**", report)
        self.assertIn("**High Risk**", report)
        self.assertIn("**Confidence:** 90%", report)

    def test_cli_writes_markdown_for_custom_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "requirements.txt"
            output = Path(directory) / "reports" / "review.md"
            source.write_text("The service returns a receipt.", encoding="utf-8")
            stdout = io.StringIO()

            with patch("app.RequirementRAG", FakeRequirementRAG):
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(
                        ["--brd", str(source), "--output", str(output)]
                    )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output.is_file())
            self.assertIn("# Requirement Gap Review", output.read_text(encoding="utf-8"))
            self.assertIn("wrote", stdout.getvalue())
            self.assertIn("indexed 1 section(s)", stdout.getvalue())

    def test_cli_reports_input_errors_without_traceback(self) -> None:
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            exit_code = main(
                ["--brd", "missing.txt", "--output", "unused.md"]
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("error:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
