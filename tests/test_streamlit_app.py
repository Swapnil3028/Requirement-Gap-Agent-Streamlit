"""Tests for the Streamlit upload loader."""

from io import BytesIO
from concurrent.futures import Future
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from app import InputError
from agents.common import Finding, Requirement
from streamlit_app import (
    AnalysisCancelled,
    AnalysisJob,
    analysis_completion_message,
    analysis_progress_caption,
    analysis_job_blocks_new_analysis,
    estimate_remaining_seconds,
    format_duration,
    upload_widget_key,
    next_upload_generation,
    build_dashboard_result,
    build_history_trend_chart,
    build_issue_breakdown_chart,
    build_quality_gauge,
    build_risk_heatmap,
    deduplicate_findings,
    extract_uploaded_text,
    finding_display_explanation,
    finding_display_title,
    finding_display_recommendation,
    format_acceptance_criteria,
    load_uploaded_requirements,
    _persistent_history_enabled,
    _analyze,
)
from utils.pdf_parser import PDFParserError, read_pdf


class FakeUploadedFile:
    def __init__(self, name: str, contents: bytes) -> None:
        self.name = name
        self._contents = contents

    def getvalue(self) -> bytes:
        return self._contents


class StreamlitUploadTests(unittest.TestCase):
    def test_persistent_history_is_disabled_unless_explicitly_enabled(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(_persistent_history_enabled())
        for value in ("1", "true", "yes"):
            with self.subTest(value=value), patch.dict(
                "os.environ",
                {"REQUIREMENT_GAP_ENABLE_SHARED_HISTORY": value},
                clear=True,
            ):
                self.assertTrue(_persistent_history_enabled())

    def test_progress_duration_formats_seconds_minutes_and_hours(self) -> None:
        self.assertEqual(format_duration(3), "3 sec")
        self.assertEqual(format_duration(60), "1 min")
        self.assertEqual(format_duration(3660), "1 hr 1 min")

    def test_remaining_time_estimate_waits_for_enough_progress(self) -> None:
        self.assertIsNone(estimate_remaining_seconds(0, 20))
        self.assertIsNone(estimate_remaining_seconds(19, 20))
        self.assertIsNone(estimate_remaining_seconds(50, 4))
        self.assertEqual(estimate_remaining_seconds(50, 20), 20)
        self.assertEqual(estimate_remaining_seconds(100, 20), 0)

    def test_progress_caption_shows_eta_and_pauses_it_when_cancelling(self) -> None:
        caption = analysis_progress_caption(72, 30)
        self.assertIn("Elapsed: 30 sec", caption)
        self.assertIn("Estimated time remaining: about 12 sec", caption)

        cancelling_caption = analysis_progress_caption(72, 30, True)
        self.assertIn("completion estimate paused", cancelling_caption)

    def test_upload_widget_key_changes_after_completed_analysis(self) -> None:
        self.assertNotEqual(upload_widget_key(0), upload_widget_key(1))

    def test_completed_analysis_advances_file_uploader_generation(self) -> None:
        generation = next_upload_generation(0)
        self.assertEqual(generation, 1)
        self.assertNotEqual(
            upload_widget_key(0),
            upload_widget_key(generation),
        )

    def test_cancelled_analysis_no_longer_disables_analyze(self) -> None:
        future: Future = Future()
        cancel_event = Event()
        job = AnalysisJob(
            future=future,
            cancel_event=cancel_event,
        )

        self.assertTrue(analysis_job_blocks_new_analysis(job))
        cancel_event.set()
        self.assertFalse(analysis_job_blocks_new_analysis(job))

    def test_completed_analysis_no_longer_disables_analyze(self) -> None:
        future: Future = Future()
        future.set_result(
            {
                "requirements": [],
                "findings": [],
                "retrieved_sections": {},
                "report": "",
                "dashboard": {
                    "score": 100,
                    "readiness_status": "Ready for Development",
                    "ambiguities": 0,
                    "missing_ac": 0,
                    "missing_business_rules": 0,
                    "missing_nfrs": 0,
                    "conflicts": 0,
                    "risk": "Low",
                },
                "document_count": 0,
            }
        )
        job = AnalysisJob(future=future, cancel_event=Event())

        self.assertFalse(analysis_job_blocks_new_analysis(job))

    def test_completed_review_renders_all_results_tabs(self) -> None:
        requirement = Requirement(
            identifier="US-1",
            source="Story.docx",
            location="line 1",
            text="As a buyer, I can view an order.",
        )
        finding = Finding(
            category="Missing acceptance criteria",
            severity="High",
            requirement=requirement,
            reason="Missing criteria for source requirement.",
            recommendation="Add source-grounded acceptance criteria.",
        )
        document_finding = Finding(
            category="Missing NFR: Security",
            severity="Medium",
            requirement=None,
            reason="No security requirement was identified in the uploaded batch.",
            recommendation="Confirm whether security criteria apply.",
            clarification_question="Does this batch need security criteria?",
            document_scope="Story.docx",
        )
        app = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "streamlit_app.py")
        )
        app.session_state["review_result"] = {
            "requirements": [requirement],
            "findings": [finding, document_finding],
            "retrieved_sections": {},
            "report": "# Test report",
            "dashboard": {
                "score": 85,
                "readiness_status": "Ready for Development",
                "ambiguities": 0,
                "missing_ac": 1,
                "missing_business_rules": 0,
                "missing_nfrs": 1,
                "conflicts": 0,
                "risk": "Medium",
            },
            "document_count": 1,
        }

        app.run(timeout=10)

        self.assertFalse(app.exception)
        self.assertEqual(
            [tab.label for tab in app.tabs],
            [
                "Findings",
                "Clarification Questions",
                "Already Answered",
                "Suggested Acceptance Criteria",
                "Download Report",
            ],
        )
        self.assertIn("Confidence", app.dataframe[0].value.columns)
        self.assertIn("Rationale", app.dataframe[0].value.columns)
        self.assertIn("Business impact", app.dataframe[0].value.columns)
        self.assertIn("Recommendation", app.dataframe[0].value.columns)

    def test_analyze_button_is_enabled_after_cancellation_request(self) -> None:
        cancel_event = Event()
        cancel_event.set()
        app = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "streamlit_app.py")
        )
        app.session_state["analysis_job"] = AnalysisJob(
            future=Future(),
            cancel_event=cancel_event,
        )

        app.run(timeout=10)

        self.assertFalse(app.exception)
        analyze_button = next(button for button in app.button if button.label == "Analyze")
        self.assertFalse(analyze_button.disabled)

    def test_analysis_completion_message_includes_document_and_requirement_counts(self) -> None:
        self.assertEqual(
            analysis_completion_message(1, 1),
            "Analysis completed successfully: reviewed 1 requirement from 1 document.",
        )
        self.assertEqual(
            analysis_completion_message(3, 5),
            "Analysis completed successfully: reviewed 5 requirements from 3 documents.",
        )

    def test_display_explanation_identifies_each_affected_requirement(self) -> None:
        requirements = [
            Requirement(
                identifier=f"US-{index}",
                source="Stories.docx",
                location=f"paragraph {index}",
                text=story,
            )
            for index, story in enumerate(
                (
                    "A customer can cancel an order.",
                    "A customer can view an invoice.",
                ),
                start=1,
            )
        ]
        findings = [
            Finding(
                category="Missing acceptance criteria",
                severity="High",
                requirement=requirement,
                reason=f"No acceptance criteria was found for “{requirement.text}”.",
                recommendation="Add criteria.",
            )
            for requirement in requirements
        ]

        explanations = [finding_display_explanation(finding) for finding in findings]

        self.assertNotEqual(explanations[0], explanations[1])
        self.assertIn(requirements[0].text, explanations[0])
        self.assertIn(requirements[1].text, explanations[1])
        self.assertIn("different interpretations", explanations[0])
        self.assertNotIn("Missing acceptance criteria.", explanations[0])

    def test_finding_display_title_uses_plain_qa_language(self) -> None:
        requirement = Requirement(
            identifier="US-1",
            source="Stories.docx",
            location="paragraph 1",
            text="As a customer, I can cancel an order.",
        )
        finding = Finding(
            category="Missing acceptance criteria",
            severity="High",
            requirement=requirement,
            reason="No acceptance criteria was found.",
            recommendation="Add criteria.",
        )

        self.assertEqual(
            finding_display_title(finding),
            "Acceptance criteria need to be defined",
        )

    def test_display_recommendations_use_the_grounded_agent_recommendation(self) -> None:
        first_requirement = Requirement(
            identifier="REQ-1",
            source="BRD.pdf",
            location="page 1",
            text="The page should load quickly.",
        )
        second_requirement = Requirement(
            identifier="REQ-2",
            source="BRD.pdf",
            location="page 2",
            text="The system should show relevant products.",
        )
        quickly_finding = Finding(
            category="Ambiguous requirement",
            severity="Medium",
            requirement=first_requirement,
            reason="Source contains quickly.",
            recommendation="Define quickly in the uploaded page requirement.",
        )
        relevant_finding = Finding(
            category="Ambiguous requirement",
            severity="Medium",
            requirement=second_requirement,
            reason="Source contains relevant.",
            recommendation="Define relevant in the uploaded product requirement.",
        )

        quickly_recommendation = finding_display_recommendation(quickly_finding)
        relevant_recommendation = finding_display_recommendation(relevant_finding)

        self.assertEqual(
            quickly_recommendation,
            "Define quickly in the uploaded page requirement.",
        )
        self.assertEqual(
            relevant_recommendation,
            "Define relevant in the uploaded product requirement.",
        )
        self.assertNotEqual(quickly_recommendation, relevant_recommendation)

    def test_display_recommendation_includes_conflicting_statement(self) -> None:
        first = Requirement(
            identifier="REQ-1",
            source="BRD.pdf",
            location="page 1",
            text="Minimum age is 18.",
        )
        second = Requirement(
            identifier="REQ-2",
            source="BRD.pdf",
            location="page 2",
            text="Minimum age is 16.",
        )
        finding = Finding(
            category="Conflicting business rules",
            severity="High",
            requirement=first,
            related_requirement=second,
            reason="Conflicting thresholds.",
            recommendation="Align both rules.",
        )

        recommendation = finding_display_recommendation(finding)

        self.assertEqual(recommendation, "Align both rules.")

    def test_deduplicate_findings_removes_exact_repeat_for_same_source(self) -> None:
        requirement = Requirement(
            identifier="REQ-1",
            source="BRD.pdf",
            source_path="streamlit-upload:BRD.pdf",
            location="paragraph 1",
            text="The system responds quickly.",
        )
        finding = Finding(
            category="Ambiguous requirement",
            severity="Medium",
            requirement=requirement,
            reason="Vague wording: quickly.",
            recommendation="Specify a measurable response time.",
        )
        repeated = Finding(
            category="Ambiguous requirement",
            severity="Medium",
            requirement=requirement,
            reason="Vague wording: quickly.",
            recommendation="Specify a measurable response time.",
        )

        result = deduplicate_findings([finding, repeated])

        self.assertEqual(result, [finding])

    def test_deduplicate_findings_keeps_different_categories(self) -> None:
        requirement = Requirement(
            identifier="REQ-1",
            source="BRD.pdf",
            source_path="streamlit-upload:BRD.pdf",
            location="paragraph 1",
            text="The system responds quickly.",
        )
        ambiguity = Finding(
            category="Ambiguous requirement",
            severity="Medium",
            requirement=requirement,
            reason="Vague wording: quickly.",
            recommendation="Specify a measurable response time.",
        )
        testability = Finding(
            category="Testability gap",
            severity="Medium",
            requirement=requirement,
            reason="No measurable result was found.",
            recommendation="Define a reproducible pass/fail condition.",
        )

        self.assertEqual(
            deduplicate_findings([ambiguity, testability]),
            [ambiguity, testability],
        )

    def test_leadership_charts_use_review_history_values(self) -> None:
        history_frame = pd.DataFrame(
            [
                {
                    "Document Name": "Checkout.pdf",
                    "Date": "2026-10-09T10:00:00+00:00",
                    "Score": 92,
                    "Ambiguities": 2,
                    "Missing AC": 1,
                    "Conflict": 0,
                    "Risk": "Low",
                },
                {
                    "Document Name": "Checkout.pdf",
                    "Date": "2026-10-10T10:00:00+00:00",
                    "Score": 80,
                    "Ambiguities": 5,
                    "Missing AC": 3,
                    "Conflict": 1,
                    "Risk": "Medium",
                },
            ]
        )

        ambiguity_chart = build_history_trend_chart(
            history_frame, "Ambiguities", "Ambiguity Trend"
        )
        heatmap = build_risk_heatmap(history_frame)

        self.assertEqual(list(ambiguity_chart.data[0].y), [2, 5])
        self.assertEqual(list(heatmap.data[0].text[0]), ["Low", "Medium"])
        self.assertEqual(list(heatmap.data[0].z[0]), [1, 2])

    def test_issue_breakdown_chart_uses_dashboard_counts(self) -> None:
        dashboard = {
            "score": 82,
            "readiness_status": "Needs Clarification",
            "score_model": "requirement_quality_v1",
            "ambiguities": 5,
            "missing_ac": 3,
            "missing_business_rules": 2,
            "missing_nfrs": 4,
            "conflicts": 1,
            "risk": "Medium",
        }

        figure = build_issue_breakdown_chart(dashboard)

        self.assertEqual(
            list(figure.data[0].x),
            [
                "Ambiguity",
                "Missing AC",
                "Missing Business Rules",
                "Missing NFRs",
                "Conflict",
            ],
        )
        self.assertEqual(list(figure.data[0].y), [5, 3, 2, 4, 1])

    def test_quality_gauge_uses_score_with_zero_to_hundred_range(self) -> None:
        figure = build_quality_gauge(82)

        self.assertEqual(figure.data[0].mode, "gauge+number")
        self.assertEqual(figure.data[0].value, 82)
        self.assertEqual(figure.data[0].title.text, "Requirement Quality Score")
        self.assertEqual(figure.data[0].gauge.axis.range, (0, 100))

    def test_acceptance_criteria_formatter_numbers_generated_scenarios(self) -> None:
        requirement = Requirement(
            identifier="US-1",
            source="Stories.txt",
            location="line 1",
            text="A customer can cancel an order.",
            acceptance_criteria=None,
        )
        finding = Finding(
            category="Missing acceptance criteria",
            severity="High",
            requirement=requirement,
            reason="No criteria specified.",
            recommendation="Add acceptance criteria.",
            generated_scenarios={
                "Positive": ("User can cancel before shipment.",),
                "Negative": ("Cancellation blocked after shipment.",),
                "Validation": ("Refund processed automatically.",),
            },
        )

        formatted = format_acceptance_criteria([requirement], [finding])

        self.assertIn("AC1:\nUser can cancel before shipment.", formatted)
        self.assertIn("AC2:\nCancellation blocked after shipment.", formatted)
        self.assertIn("AC3:\nRefund processed automatically.", formatted)

    def test_acceptance_criteria_formatter_includes_existing_criteria(self) -> None:
        requirement = Requirement(
            identifier="US-2",
            source="Stories.txt",
            location="line 2",
            text="A customer can view an order.",
            acceptance_criteria="Order details are displayed.",
        )

        formatted = format_acceptance_criteria([requirement], [])

        self.assertIn(
            "Existing acceptance criteria — US-2:\nOrder details are displayed.",
            formatted,
        )

    def test_dashboard_result_summarizes_findings_and_derives_risk(self) -> None:
        requirement = Requirement(
            identifier="REQ-1",
            source="BRD.txt",
            location="line 1",
            text="Requirement text.",
        )
        findings = [
            Finding(
                category="Ambiguous requirement",
                severity="Medium",
                requirement=requirement,
                reason="Vague wording.",
                recommendation="Clarify it.",
            ),
            Finding(
                category="Missing acceptance criteria",
                severity="High",
                requirement=requirement,
                reason="No criteria.",
                recommendation="Add criteria.",
            ),
            Finding(
                category="Conflicting business rules",
                severity="High",
                requirement=requirement,
                reason="Conflicting rules.",
                recommendation="Resolve them.",
            ),
        ]

        result = build_dashboard_result(findings)

        self.assertEqual(
            result,
            {
                "score": 65,
                "score_model": "requirement_quality_v1",
                "readiness_status": "Needs Clarification",
                "ambiguities": 1,
                "missing_ac": 1,
                "missing_business_rules": 0,
                "missing_nfrs": 0,
                "conflicts": 1,
                "risk": "High",
            },
        )

    def test_no_findings_produces_full_score_and_low_risk(self) -> None:
        self.assertEqual(
            build_dashboard_result([]),
            {
                "score": 100,
                "score_model": "requirement_quality_v1",
                "readiness_status": "Ready for Development",
                "ambiguities": 0,
                "missing_ac": 0,
                "missing_business_rules": 0,
                "missing_nfrs": 0,
                "conflicts": 0,
                "risk": "Low",
            },
        )

    def test_critical_finding_sets_critical_risk(self) -> None:
        requirement = Requirement(
            identifier="REQ-1",
            source="BRD.txt",
            location="line 1",
            text="Requirement text.",
        )
        finding = Finding(
            category="Ambiguous requirement",
            severity="Critical",
            requirement=requirement,
            reason="Critical issue.",
            recommendation="Resolve immediately.",
        )

        result = build_dashboard_result([finding])

        self.assertEqual(result["score"], 90)
        self.assertEqual(result["risk"], "Critical")

    def test_quality_score_readiness_cutoffs_are_exact(self) -> None:
        requirement = Requirement(
            identifier="REQ-1",
            source="BRD.txt",
            location="line 1",
            text="Requirement text.",
        )
        ambiguity = Finding(
            category="Ambiguous requirement",
            severity="Medium",
            requirement=requirement,
            reason="Vague.",
            recommendation="Clarify.",
        )
        missing_ac = Finding(
            category="Missing acceptance criteria",
            severity="High",
            requirement=requirement,
            reason="Missing criteria.",
            recommendation="Add criteria.",
        )
        conflicts = Finding(
            category="Conflicting business rules",
            severity="High",
            requirement=requirement,
            reason="Conflict.",
            recommendation="Resolve.",
        )

        self.assertEqual(build_dashboard_result([ambiguity, missing_ac])["score"], 80)
        self.assertEqual(
            build_dashboard_result([ambiguity, missing_ac, conflicts])["score"], 65
        )
        self.assertEqual(
            build_dashboard_result([ambiguity] * 5)["readiness_status"],
            "Needs Clarification",
        )
        self.assertEqual(
            build_dashboard_result([ambiguity] * 6)["readiness_status"],
            "High Risk",
        )
        nfr_findings = [
            Finding(
                category=f"Missing NFR: {area}",
                severity="Medium",
                requirement=None,
                reason="Not identified in the uploaded batch.",
                recommendation="Confirm whether criteria apply.",
                document_scope="BRD.pdf",
            )
            for area in ("Performance", "Security", "Availability")
        ]
        self.assertEqual(build_dashboard_result(nfr_findings)["score"], 85)
        self.assertEqual(
            build_dashboard_result(nfr_findings)["readiness_status"],
            "Ready for Development",
        )

    def test_read_pdf_extracts_text_from_file_like_object(self) -> None:
        import pymupdf

        pdf = pymupdf.open()
        page = pdf.new_page()
        page.insert_text((72, 72), "The system displays a receipt.")
        uploaded = BytesIO(pdf.tobytes())
        pdf.close()

        self.assertEqual(
            read_pdf(uploaded), "The system displays a receipt."
        )

    def test_read_pdf_rejects_empty_upload(self) -> None:
        with self.assertRaisesRegex(PDFParserError, "empty"):
            read_pdf(BytesIO(b""))

    def test_extract_uploaded_text_supports_text_uploads(self) -> None:
        uploaded = FakeUploadedFile("BRD.txt", b"The system displays a receipt.")

        self.assertEqual(
            extract_uploaded_text(uploaded), "The system displays a receipt."
        )

    def test_loads_upload_and_uses_stable_rag_source(self) -> None:
        uploaded = FakeUploadedFile(
            "BRD.txt", b"The system should load quickly."
        )

        requirements = load_uploaded_requirements([uploaded])

        self.assertEqual(len(requirements), 1)
        self.assertEqual(requirements[0].source, "BRD.txt")
        self.assertEqual(
            requirements[0].source_path, "streamlit-upload:BRD.txt"
        )

    def test_upload_loader_reports_progress_after_each_document(self) -> None:
        uploads = [
            FakeUploadedFile("BRD-A.txt", b"Minimum age is 18."),
            FakeUploadedFile("BRD-B.txt", b"Minimum age is 16."),
        ]
        progress_updates = []

        load_uploaded_requirements(
            uploads,
            lambda percentage, message: progress_updates.append(
                (percentage, message)
            ),
        )

        percentages = [percentage for percentage, _ in progress_updates]
        self.assertEqual(percentages[-1], 35)
        self.assertEqual(percentages, sorted(percentages))
        messages = [message for _, message in progress_updates]
        self.assertIn("Loaded document 1 of 2.", messages)
        self.assertIn("Loaded document 2 of 2.", messages)

    def test_upload_loader_stops_at_the_next_document_after_cancel(self) -> None:
        uploads = [
            FakeUploadedFile("BRD-A.txt", b"Minimum age is 18."),
            FakeUploadedFile("BRD-B.txt", b"Minimum age is 16."),
        ]
        cancel_event = Event()

        def cancel_after_first_document(percentage: int, message: str) -> None:
            if message == "Loaded document 1 of 2.":
                cancel_event.set()

        with self.assertRaises(AnalysisCancelled):
            load_uploaded_requirements(
                uploads,
                cancel_after_first_document,
                cancel_event,
            )

    def test_analysis_reports_progress_for_each_processing_stage(self) -> None:
        class FakeRAG:
            def __init__(self, persist_directory):
                pass

            def index_requirements(self, requirements):
                return len(requirements)

            def retrieve_for_requirements(self, requirements, k):
                return {}

            def close(self):
                pass

        requirement = Requirement(
            identifier="REQ-1",
            source="BRD.txt",
            location="line 1",
            text="The system displays a receipt.",
            source_path="streamlit-upload:BRD.txt",
        )
        progress_updates = []

        with patch("streamlit_app.RequirementRAG", FakeRAG):
            _findings, retrieved_sections, report = _analyze(
                [requirement],
                lambda percentage, message: progress_updates.append(
                    (percentage, message)
                ),
            )

        self.assertEqual(
            [percentage for percentage, _ in progress_updates],
            [38, 42, 58, 72, 90],
        )
        self.assertIn("# Requirement Gap Review", report)
        self.assertEqual(retrieved_sections, {"streamlit-upload:BRD.txt::REQ-1": []})

    def test_analysis_honors_cancellation_and_closes_rag(self) -> None:
        cancel_event = Event()
        closed = []

        class FakeRAG:
            def __init__(self, persist_directory):
                pass

            def index_requirements(self, requirements):
                raise AssertionError("Indexing should not begin after cancellation.")

            def close(self):
                closed.append(True)

        requirement = Requirement(
            identifier="REQ-1",
            source="BRD.txt",
            location="line 1",
            text="The system displays a receipt.",
        )

        def cancel_at_first_stage(percentage: int, message: str) -> None:
            if percentage == 42:
                cancel_event.set()

        with patch("streamlit_app.RequirementRAG", FakeRAG):
            with self.assertRaises(AnalysisCancelled):
                _analyze(
                    [requirement],
                    cancel_at_first_stage,
                    cancel_event,
                )

        self.assertEqual(closed, [True])

    def test_loads_batch_uploads_and_preserves_each_source(self) -> None:
        uploads = [
            FakeUploadedFile("BRD-A.txt", b"Minimum age is 18."),
            FakeUploadedFile("BRD-B.txt", b"Minimum age is 16."),
        ]

        requirements = load_uploaded_requirements(uploads)

        self.assertEqual(len(requirements), 2)
        self.assertEqual(
            [requirement.source for requirement in requirements],
            ["BRD-A.txt", "BRD-B.txt"],
        )
        self.assertEqual(
            [requirement.source_path for requirement in requirements],
            ["streamlit-upload:BRD-A.txt", "streamlit-upload:BRD-B.txt"],
        )

    def test_empty_upload_raises_actionable_error(self) -> None:
        uploaded = FakeUploadedFile("BRD.docx", b"")

        with self.assertRaisesRegex(InputError, "empty"):
            load_uploaded_requirements([uploaded])

    def test_duplicate_filenames_are_rejected(self) -> None:
        uploads = [
            FakeUploadedFile("BRD.txt", b"First requirement."),
            FakeUploadedFile("brd.TXT", b"Second requirement."),
        ]

        with self.assertRaisesRegex(InputError, "Rename the files"):
            load_uploaded_requirements(uploads)


if __name__ == "__main__":
    unittest.main()
