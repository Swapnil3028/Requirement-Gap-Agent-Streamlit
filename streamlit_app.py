"""Streamlit interface for the requirement review agent."""

from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from io import BytesIO
import logging
import math
import os
from pathlib import Path
import re
from threading import Event, Lock
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Protocol, TypedDict

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from agents.common import Finding, Requirement
from agents.already_answered_agent import AlreadyAnsweredQuestion
from agents.domain_detection import BusinessDomainAssessment, detect_business_domain
from agents.finding_presentation import (
    finding_display_explanation,
    finding_display_title,
)
from agents.human_tone_agent import HumanToneRewriteAgent, HumanizedFinding
from agents.quality_score import (
    QUALITY_SCORE_MODEL,
    assess_requirement_quality,
    readiness_status_for_score,
)
from app import (
    InputError,
    load_requirements,
    render_report,
    restrict_retrieved_sections,
    review_requirements_with_context,
)
from agents.clarification_agent import generate_clarification_questions
from agents.deduplication_agent import DeduplicationAgent
from document_parser import DocumentParserError, extract_text
from history_store import (
    ReviewHistoryEntry,
    ReviewHistoryError,
    load_review_history,
    save_review,
)
from rag_store import RequirementRAG, RequirementRAGError, RetrievedSection
from utils.pdf_parser import PDFParserError, read_pdf


class DashboardResult(TypedDict):
    score: int
    score_model: str
    readiness_status: str
    ambiguities: int
    missing_ac: int
    missing_business_rules: int
    missing_nfrs: int
    conflicts: int
    risk: str


class AnalysisResult(TypedDict):
    requirements: list[Requirement]
    findings: list[Finding]
    retrieved_sections: dict[str, list[RetrievedSection]]
    report: str
    dashboard: DashboardResult
    document_count: int
    answered_questions: list[AlreadyAnsweredQuestion]
    domain_assessment: BusinessDomainAssessment
    humanized_findings: list[HumanizedFinding]


_SEVERITY_PENALTIES = {
    "Critical": 25,
    "High": 15,
    "Medium": 8,
    "Low": 3,
}


class UploadedDocument(Protocol):
    """The subset of Streamlit's uploaded-file API used by the loader."""

    name: str

    def getvalue(self) -> bytes: ...


ProgressCallback = Callable[[int, str], None]
_ANALYSIS_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="requirement-review")
_LOGGER = logging.getLogger(__name__)
_RAG_LOCK = Lock()


def _persistent_history_enabled() -> bool:
    """Enable shared history only when explicitly requested by the operator."""
    return os.environ.get("REQUIREMENT_GAP_ENABLE_SHARED_HISTORY", "").casefold() in {
        "1",
        "true",
        "yes",
    }


def format_duration(seconds: float) -> str:
    """Format a duration for compact progress feedback."""
    remaining = max(0, math.ceil(seconds))
    if remaining < 60:
        return f"{remaining} sec"
    minutes, seconds = divmod(remaining, 60)
    if minutes < 60:
        return f"{minutes} min {seconds} sec" if seconds else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} hr {minutes} min" if minutes else f"{hours} hr"


def estimate_remaining_seconds(percentage: int, elapsed_seconds: float) -> float | None:
    """Estimate remaining time by extrapolating the elapsed progress rate."""
    if percentage >= 100:
        return 0.0
    if percentage < 20 or elapsed_seconds < 5:
        return None
    return elapsed_seconds * (100 - percentage) / percentage


def analysis_progress_caption(
    percentage: int,
    elapsed_seconds: float,
    cancellation_requested: bool = False,
) -> str:
    """Describe elapsed time and the current completion-time estimate."""
    elapsed = format_duration(elapsed_seconds)
    if cancellation_requested:
        estimate_label = (
            "Stopping after the current operation; completion estimate paused."
        )
    elif percentage >= 100:
        estimate_label = "Analysis complete."
    else:
        remaining = estimate_remaining_seconds(percentage, elapsed_seconds)
        if remaining is None:
            estimate_label = "Estimating completion time as processing progresses."
        else:
            estimate_label = (
                f"Estimated time remaining: about {format_duration(remaining)} "
                "(rough estimate; updates as analysis progresses)."
            )
    return f"Elapsed: {elapsed} · {estimate_label}"


class AnalysisCancelled(Exception):
    """Raised when a user requests cancellation between analysis stages."""


@dataclass
class AnalysisJob:
    future: Future[AnalysisResult]
    cancel_event: Event
    progress: dict[str, object] = field(
        default_factory=lambda: {"percentage": 0, "message": "Starting analysis..."}
    )
    lock: Lock = field(default_factory=Lock)
    superseded: bool = False
    started_at: float = field(default_factory=monotonic)


@dataclass(frozen=True)
class UploadedDocumentBytes:
    """Immutable upload snapshot safe to pass to an analysis worker."""

    name: str
    contents: bytes

    def getvalue(self) -> bytes:
        return self.contents


def _check_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise AnalysisCancelled("Analysis was cancelled at a safe stage boundary.")


def analysis_job_blocks_new_analysis(job: object) -> bool:
    """Allow a replacement review once cancellation or completion is signaled."""
    return (
        isinstance(job, AnalysisJob)
        and not job.cancel_event.is_set()
        and not job.future.done()
    )


def upload_widget_key(generation: int) -> str:
    """Return a fresh file-uploader key for each completed analysis."""
    return f"requirements-upload-{generation}"


def next_upload_generation(generation: int) -> int:
    """Advance the uploader key after a successful review."""
    return generation + 1


def _log_superseded_analysis_failure(job: AnalysisJob) -> None:
    if not job.superseded:
        return
    try:
        job.future.result()
    except AnalysisCancelled:
        return
    except Exception as exc:
        _LOGGER.error(
            "A superseded requirement review failed while stopping",
            exc_info=(type(exc), exc, exc.__traceback__),
        )


def deduplicate_findings(findings: Sequence[Finding]) -> list[Finding]:
    """Consolidate duplicate findings through the dedicated review agent."""
    return DeduplicationAgent().consolidate(findings)


def finding_display_recommendation(finding: Finding) -> str:
    """Display the recommendation produced from the uploaded requirement evidence."""
    return finding.recommendation


def analysis_completion_message(document_count: int, requirement_count: int) -> str:
    """Summarize a completed analysis for the dashboard."""
    documents_label = "document" if document_count == 1 else "documents"
    requirements_label = "requirement" if requirement_count == 1 else "requirements"
    return (
        f"Analysis completed successfully: reviewed {requirement_count} "
        f"{requirements_label} from {document_count} {documents_label}."
    )


def build_dashboard_result(findings: Sequence[Finding]) -> DashboardResult:
    """Summarize findings using the requirement-quality scoring model."""
    unknown_severities = {
        finding.severity
        for finding in findings
        if finding.severity not in _SEVERITY_PENALTIES
    }
    if unknown_severities:
        raise ValueError(
            f"Unsupported finding severity: {', '.join(sorted(unknown_severities))}"
        )

    assessment = assess_requirement_quality(findings)
    severity_score = max(
        0,
        100 - sum(_SEVERITY_PENALTIES[finding.severity] for finding in findings),
    )
    if any(finding.severity == "Critical" for finding in findings) or severity_score < 40:
        risk = "Critical"
    elif severity_score < 70:
        risk = "High"
    elif severity_score < 90:
        risk = "Medium"
    else:
        risk = "Low"

    return {
        "score": assessment.score,
        "score_model": QUALITY_SCORE_MODEL,
        "readiness_status": assessment.readiness_status,
        "ambiguities": sum(
            finding.category == "Ambiguous requirement" for finding in findings
        ),
        "missing_ac": sum(
            finding.category == "Missing acceptance criteria" for finding in findings
        ),
        "conflicts": sum(
            finding.category == "Conflicting business rules" for finding in findings
        ),
        "missing_business_rules": assessment.missing_business_rules,
        "missing_nfrs": assessment.missing_nfrs,
        "risk": risk,
    }


def build_quality_gauge(score: int) -> go.Figure:
    """Build the Plotly gauge for the current requirement quality score."""
    return go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=score,
            title={"text": "Requirement Quality Score"},
            gauge={"axis": {"range": [0, 100]}},
        )
    )


def build_issue_breakdown_chart(dashboard: DashboardResult) -> go.Figure:
    """Build a bar chart of issue counts across reviewed requirement areas."""
    data = pd.DataFrame(
        {
            "Category": [
                "Ambiguity",
                "Missing AC",
                "Missing Business Rules",
                "Missing NFRs",
                "Conflict",
            ],
            "Count": [
                dashboard["ambiguities"],
                dashboard["missing_ac"],
                dashboard["missing_business_rules"],
                dashboard["missing_nfrs"],
                dashboard["conflicts"],
            ],
        }
    )
    return px.bar(
        data,
        x="Category",
        y="Count",
        title="Issue Breakdown",
    )


def build_history_trend_chart(
    history_frame: pd.DataFrame,
    metric: str,
    title: str,
) -> go.Figure:
    """Build a trend line for a metric over saved review runs."""
    return px.line(
        history_frame.sort_values("Date"),
        x="Date",
        y=metric,
        color="Document Name",
        markers=True,
        title=title,
    )


def build_risk_heatmap(history_frame: pd.DataFrame) -> go.Figure:
    """Build a heatmap showing saved risk levels for each review run."""
    risk_values = {"Unknown": 0, "Low": 1, "Medium": 2, "High": 3, "Critical": 4}
    ordered = history_frame.sort_values("Date").reset_index(drop=True).copy()
    ordered["Run"] = [
        f"{name} — {date}"
        for name, date in zip(ordered["Document Name"], ordered["Date"])
    ]
    ordered["Risk Value"] = ordered["Risk"].map(risk_values)
    figure = go.Figure(
        go.Heatmap(
            x=ordered["Run"],
            y=["Risk"] * len(ordered),
            z=[ordered["Risk Value"].tolist()],
            text=[ordered["Risk"].tolist()],
            texttemplate="%{text}",
            zmin=0,
            zmax=4,
            colorscale=[
                [0.0, "#cbd5e1"],
                [0.25, "#22c55e"],
                [0.5, "#eab308"],
                [0.75, "#f97316"],
                [1.0, "#ef4444"],
            ],
            colorbar={
                "tickvals": [0, 1, 2, 3, 4],
                "ticktext": ["Unknown", "Low", "Medium", "High", "Critical"],
                "title": "Risk",
            },
        )
    )
    figure.update_layout(title="Risk Heat Map")
    return figure


def format_acceptance_criteria(
    requirements: Sequence[Requirement],
    findings: Sequence[Finding],
) -> str:
    """Format existing and generated acceptance criteria for the UI."""
    sections = []
    for requirement in requirements:
        if requirement.acceptance_criteria:
            sections.append(
                f"Existing acceptance criteria — {requirement.identifier}:\n"
                f"{requirement.acceptance_criteria}"
            )

    suggestion_number = 1
    for finding in findings:
        if not finding.generated_scenarios:
            continue
        criteria = []
        for scenarios in finding.generated_scenarios.values():
            for scenario in scenarios:
                criteria.append(f"AC{suggestion_number}:\n{scenario}")
                suggestion_number += 1
        if criteria:
            identifier = (
                finding.requirement.identifier
                if finding.requirement is not None
                else finding.document_scope or "document-level finding"
            )
            sections.append(
                f"Suggested acceptance criteria — "
                f"{identifier}:\n\n"
                + "\n\n".join(criteria)
            )
    return "\n\n".join(sections)


def extract_uploaded_text(uploaded_file: UploadedDocument) -> str:
    """Extract text from an uploaded PDF, DOCX, or TXT file."""
    filename = Path(uploaded_file.name).name
    if not filename:
        raise InputError("An uploaded file must have a filename.")
    if Path(filename).suffix.lower() == ".pdf":
        return read_pdf(BytesIO(uploaded_file.getvalue()))

    with TemporaryDirectory(prefix="requirement-gap-agent-") as directory:
        source_path = Path(directory) / filename
        source_path.write_bytes(uploaded_file.getvalue())
        try:
            return extract_text(source_path)
        except DocumentParserError as exc:
            raise InputError(str(exc)) from exc


def load_uploaded_requirements(
    uploaded_files: Sequence[UploadedDocument],
    progress_callback: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> list[Requirement]:
    """Load uploaded files through the same parsers as the CLI."""
    requirements = []
    seen_names = set()
    with TemporaryDirectory(prefix="requirement-gap-agent-") as directory:
        for index, uploaded_file in enumerate(uploaded_files, start=1):
            _check_cancelled(cancel_event)
            if progress_callback is not None:
                progress = 5 + round(30 * (index - 1) / len(uploaded_files))
                progress_callback(
                    progress,
                    f"Loading document {index} of {len(uploaded_files)}...",
                )
            _check_cancelled(cancel_event)
            filename = Path(uploaded_file.name).name
            if not filename:
                raise InputError("An uploaded file must have a filename.")
            normalized_name = filename.casefold()
            if normalized_name in seen_names:
                raise InputError(
                    f"More than one uploaded file is named '{filename}'. "
                    "Rename the files so each name is unique."
                )
            seen_names.add(normalized_name)

            source_path = Path(directory) / filename
            source_path.write_bytes(uploaded_file.getvalue())
            requirements.extend(
                Requirement(
                    identifier=requirement.identifier,
                    source=requirement.source,
                    location=requirement.location,
                    text=requirement.text,
                    acceptance_criteria=requirement.acceptance_criteria,
                    source_path=f"streamlit-upload:{filename}",
                )
                for requirement in load_requirements(source_path)
            )
            _check_cancelled(cancel_event)
            if progress_callback is not None:
                progress = 5 + round(30 * index / len(uploaded_files))
                progress_callback(
                    progress,
                    f"Loaded document {index} of {len(uploaded_files)}.",
                )
    return requirements


def _analyze(
    requirements: Sequence[Requirement],
    progress_callback: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> tuple[list[Finding], dict[str, list[RetrievedSection]], str]:
    findings, retrieved_sections, report, _, _, _ = _analyze_with_audit(
        requirements,
        progress_callback,
        cancel_event,
    )
    return findings, retrieved_sections, report


def _analyze_with_audit(
    requirements: Sequence[Requirement],
    progress_callback: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> tuple[
    list[Finding],
    dict[str, list[RetrievedSection]],
    str,
    list[AlreadyAnsweredQuestion],
    BusinessDomainAssessment,
    list[HumanizedFinding],
]:
    if progress_callback is not None:
        progress_callback(38, "Waiting for the local review index to be available...")
    while not _RAG_LOCK.acquire(timeout=0.25):
        _check_cancelled(cancel_event)
    rag_workspace = None
    try:
        rag_workspace = TemporaryDirectory(prefix="requirement-gap-rag-")
        rag_directory = Path(rag_workspace.name) / "chroma"
        _check_cancelled(cancel_event)
        rag = RequirementRAG(rag_directory)
        if progress_callback is not None:
            progress_callback(
                42,
                "Indexing document text in the local search database...",
            )
        _check_cancelled(cancel_event)
        rag.index_requirements(requirements)
        _check_cancelled(cancel_event)
        if progress_callback is not None:
            progress_callback(
                58,
                "Finding related requirement statements for comparison...",
            )
        _check_cancelled(cancel_event)
        retrieved_sections = restrict_retrieved_sections(
            requirements,
            rag.retrieve_for_requirements(requirements, k=3),
        )
        _check_cancelled(cancel_event)
        if progress_callback is not None:
            progress_callback(
                72,
                "Checking requirement gaps and whether the full document already answers them...",
            )
        _check_cancelled(cancel_event)
        findings, answered_questions, domain_assessment = review_requirements_with_context(
            requirements,
            retrieved_sections,
        )
        humanized_findings = HumanToneRewriteAgent().rewrite_all(findings)
        _check_cancelled(cancel_event)
        if progress_callback is not None:
            progress_callback(
                90,
                "Rewriting findings in a Senior QA voice and preparing the report...",
            )
        _check_cancelled(cancel_event)
        report = render_report(
            requirements,
            findings,
            retrieved_sections,
            answered_questions,
            domain_assessment,
            humanized_findings,
        )
        _check_cancelled(cancel_event)
    finally:
        try:
            if "rag" in locals():
                rag.close()
        finally:
            try:
                if rag_workspace is not None:
                    rag_workspace.cleanup()
            finally:
                _RAG_LOCK.release()
    return (
        findings,
        retrieved_sections,
        report,
        answered_questions,
        domain_assessment,
        humanized_findings,
    )


def _create_analysis_job(
    uploaded_files: Sequence[UploadedDocument],
) -> AnalysisJob:
    """Snapshot uploads and submit analysis without blocking the Streamlit script."""
    upload_snapshots = [
        UploadedDocumentBytes(uploaded_file.name, uploaded_file.getvalue())
        for uploaded_file in uploaded_files
    ]
    cancel_event = Event()
    progress: dict[str, object] = {
        "percentage": 0,
        "message": "Preparing uploaded files and starting analysis...",
    }
    progress_lock = Lock()

    def report_progress(percentage: int, message: str) -> None:
        with progress_lock:
            progress["percentage"] = percentage
            progress["message"] = message

    def run_analysis() -> AnalysisResult:
        requirements = load_uploaded_requirements(
            upload_snapshots,
            report_progress,
            cancel_event,
        )
        _check_cancelled(cancel_event)
        (
            findings,
            retrieved_sections,
            report,
            answered_questions,
            domain_assessment,
            humanized_findings,
        ) = _analyze_with_audit(
            requirements,
            report_progress,
            cancel_event,
        )
        _check_cancelled(cancel_event)
        report_progress(94, "Calculating the quality score and readiness status...")
        dashboard_result = build_dashboard_result(findings)
        _check_cancelled(cancel_event)
        document_name = ", ".join(uploaded_file.name for uploaded_file in upload_snapshots)
        if _persistent_history_enabled():
            report_progress(97, "Saving this review to local history...")
            _check_cancelled(cancel_event)
            save_review(document_name, dashboard_result)
        else:
            report_progress(97, "Finalizing this session's review...")
        report_progress(100, "Analysis completed. 100%")
        return {
            "requirements": requirements,
            "findings": findings,
            "retrieved_sections": retrieved_sections,
            "report": report,
            "dashboard": dashboard_result,
            "document_count": len(upload_snapshots),
            "answered_questions": answered_questions,
            "domain_assessment": domain_assessment,
            "humanized_findings": humanized_findings,
        }

    future = _ANALYSIS_EXECUTOR.submit(run_analysis)
    job = AnalysisJob(
        future=future,
        cancel_event=cancel_event,
        progress=progress,
        lock=progress_lock,
    )
    future.add_done_callback(lambda _: _log_superseded_analysis_failure(job))
    return job


@st.fragment(run_every="500ms")
def _render_analysis_progress() -> None:
    job = st.session_state.get("analysis_job")
    if not isinstance(job, AnalysisJob):
        return

    with job.lock:
        percentage = int(job.progress["percentage"])
        message = str(job.progress["message"])
    st.progress(percentage, text=f"{message} {percentage}%")
    elapsed_seconds = monotonic() - job.started_at
    st.caption(
        analysis_progress_caption(
            percentage,
            elapsed_seconds,
            job.cancel_event.is_set(),
        )
    )

    if percentage < 97 and not job.cancel_event.is_set():
        if st.button("Cancel analysis", key="cancel-analysis"):
            job.cancel_event.set()
            with job.lock:
                job.progress["message"] = (
                    "Cancellation requested; stopping after the current operation..."
                )
            st.warning(
                "Cancellation requested. The current file or database operation "
                "will finish before analysis stops. You can start a new analysis "
                "after uploading another document; it will wait for this operation "
                "to release shared resources."
            )
            st.rerun()
    elif job.cancel_event.is_set():
        st.warning(
            "Cancellation requested. The current file or database operation "
            "will finish before analysis stops. You can start a new analysis "
            "after uploading another document; it will wait for this operation "
            "to release shared resources."
        )
    else:
        st.caption("Finalizing review history; cancellation is no longer available.")

    if not job.future.done():
        return

    try:
        result = job.future.result()
    except AnalysisCancelled:
        st.session_state["analysis_notice"] = (
            "Analysis cancelled. No report was produced."
        )
        st.session_state.pop("analysis_job", None)
        st.rerun()
    except (InputError, OSError, RequirementRAGError, ReviewHistoryError) as exc:
        st.session_state["analysis_notice"] = f"Review could not be completed: {exc}"
        st.session_state["analysis_notice_is_error"] = True
        st.session_state.pop("analysis_job", None)
        st.rerun()
    except Exception as exc:
        _LOGGER.exception("Unexpected failure while analyzing uploaded requirements")
        st.session_state["analysis_notice"] = (
            f"Review could not be completed due to an unexpected error: {exc}"
        )
        st.session_state["analysis_notice_is_error"] = True
        st.session_state.pop("analysis_job", None)
        st.rerun()
    else:
        st.session_state["review_result"] = result
        st.session_state["upload_generation"] = next_upload_generation(
            st.session_state.get("upload_generation", 0)
        )
        st.session_state.pop("analysis_job", None)
        st.session_state.pop("analysis_notice", None)
        st.session_state.pop("analysis_notice_is_error", None)
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="Requirement Review Agent", layout="wide")
    st.markdown(
        """
        <style>
        a {
            text-decoration: none;
            color: #464feb;
        }
        tr th, tr td {
            border: 1px solid #e6e6e6;
        }
        tr th {
            background-color: #f5f5f5;
        }
        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #e6eaf0;
            border-radius: 0.75rem;
            padding: 1rem 1.1rem;
            box-shadow: 0 2px 8px rgba(20, 35, 60, 0.05);
        }
        [data-testid="stMetricLabel"] {
            color: #5b6472;
            font-weight: 600;
        }
        [data-testid="stMetricValue"] {
            color: #202938;
        }
        div.stButton > button[kind="secondary"] {
            background: #464feb;
            color: #ffffff;
            border: 0;
            border-radius: 0.5rem;
            font-weight: 600;
            min-height: 2.8rem;
        }
        div.stButton > button[kind="secondary"]:hover {
            background: #363dc7;
            color: #ffffff;
            border: 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.title("Requirement Gap & Ambiguity Detection Dashboard")
    st.write(
        "Upload one or more BRD PDFs or DOCX files for batch analysis of ambiguity, "
        "missing acceptance criteria, conflicting business rules, and testability gaps."
    )

    upload_generation = st.session_state.setdefault("upload_generation", 0)
    uploaded_files = st.file_uploader(
        "Upload BRDs",
        type=["pdf", "docx"],
        accept_multiple_files=True,
        key=upload_widget_key(upload_generation),
    )
    current_job = st.session_state.get("analysis_job")
    analyze_clicked = st.button(
        "Analyze",
        disabled=analysis_job_blocks_new_analysis(current_job),
    )
    for uploaded_file in uploaded_files or []:
        try:
            extracted_text = extract_uploaded_text(uploaded_file)
            with st.expander(f"Extracted Content — {uploaded_file.name}"):
                st.text_area(
                    f"Requirement Text — {uploaded_file.name}",
                    extracted_text,
                    height=300,
                    key=f"extracted-{uploaded_file.name}",
                )
        except (InputError, OSError, PDFParserError) as exc:
            st.error(f"Could not extract {uploaded_file.name}: {exc}")

    if analyze_clicked:
        previous_job = st.session_state.get("analysis_job")
        if isinstance(previous_job, AnalysisJob):
            previous_job.superseded = True
            previous_job.cancel_event.set()
        st.session_state.pop("review_result", None)
        st.session_state.pop("analysis_job", None)
        st.session_state.pop("analysis_notice", None)
        st.session_state.pop("analysis_notice_is_error", None)
        if not uploaded_files:
            st.error("Upload at least one BRD PDF or DOCX file to start the review.")
        else:
            try:
                st.session_state["analysis_job"] = _create_analysis_job(uploaded_files)
            except (OSError, RuntimeError) as exc:
                st.error(f"Could not start the review: {exc}")

    if isinstance(st.session_state.get("analysis_job"), AnalysisJob):
        _render_analysis_progress()

    notice = st.session_state.get("analysis_notice")
    if notice:
        if st.session_state.get("analysis_notice_is_error"):
            st.error(notice)
        else:
            st.warning(notice)

    result = st.session_state.get("review_result")
    if result is not None:
        requirements: list[Requirement] = result["requirements"]
        findings: list[Finding] = result["findings"]
        humanized_findings = result.get("humanized_findings")
        if humanized_findings is None:
            humanized_findings = HumanToneRewriteAgent().rewrite_all(findings)
        dashboard: DashboardResult = result["dashboard"]
        domain_assessment = result.get("domain_assessment") or detect_business_domain(
            requirements
        )
        st.success(
            analysis_completion_message(
                result["document_count"],
                len(result["requirements"]),
            )
        )
        st.divider()
        st.subheader("Analysis Overview")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Requirement Quality Score", f"{dashboard['score']}/100")
        col2.metric("Readiness", dashboard["readiness_status"])
        col3.metric("Ambiguities", dashboard["ambiguities"])
        col4.metric("Missing AC", dashboard["missing_ac"])
        col5, col6, col7 = st.columns(3)
        col5.metric("Missing Business Rules", dashboard["missing_business_rules"])
        col6.metric("Missing NFR Areas", dashboard["missing_nfrs"])
        col7.metric("Conflicts", dashboard["conflicts"])
        st.caption(
            "Quality deductions: 10 per ambiguity, 10 per missing acceptance "
            "criteria, 5 per missing business rule, 5 per missing NFR area, and "
            "15 per conflict. Readiness: High Risk below 50; Needs Clarification "
            "50–84; Ready for Development 85–100. Severity-weighted legacy risk: "
            f"{dashboard['risk']}."
        )
        with st.expander("Business domain context and QA prompts", expanded=False):
            if domain_assessment.domain == "Undetermined":
                st.write("**Domain:** Undetermined")
                if domain_assessment.alternative_domains:
                    st.write(
                        "**Possible matches:** "
                        + ", ".join(domain_assessment.alternative_domains)
                    )
            else:
                st.write(
                    f"**Likely domain:** {domain_assessment.domain} "
                    f"({domain_assessment.confidence}% heuristic confidence)"
                )
            st.caption(
                "Domain is inferred from the full uploaded batch. These prompts "
                "are optional review guidance, not findings or score deductions."
            )
            if domain_assessment.evidence:
                st.write("**Document clues:** " + ", ".join(domain_assessment.evidence))
            for question in domain_assessment.review_questions:
                st.write(f"- {question}")
        st.divider()
        st.subheader("Gauge Chart")
        st.plotly_chart(
            build_quality_gauge(dashboard["score"]),
            use_container_width=True,
        )
        st.divider()
        st.subheader("Issue Breakdown Chart")
        st.plotly_chart(
            build_issue_breakdown_chart(dashboard),
            use_container_width=True,
        )

        st.divider()
        tab1, tab2, tab3, tab4, tab5 = st.tabs(
            [
                "Findings",
                "Clarification Questions",
                "Already Answered",
                "Suggested Acceptance Criteria",
                "Download Report",
            ]
        )
        with tab1:
            finding_rows = [
                {
                    "Finding": presentation.title,
                    "Also identified as": ", ".join(finding.merged_categories),
                    "Requirement": (
                        finding.requirement.text
                        if finding.requirement is not None
                        else "Document-level coverage gap"
                    ),
                    "Source": (
                        "; ".join(
                            f"{requirement.source} — {requirement.location}"
                            for requirement in (
                                *((finding.requirement,) if finding.requirement else ()),
                                *finding.supporting_requirements,
                            )
                        )
                        if finding.requirement is not None
                        else finding.document_scope or "Uploaded document batch"
                    ),
                    "Rationale": presentation.rationale,
                    "Business impact": presentation.business_impact,
                    "Severity": finding.severity,
                    "Confidence": f"{finding.confidence_score}%",
                    "Recommendation": presentation.recommendation,
                }
                for presentation in humanized_findings
                for finding in (presentation.finding,)
            ]
            if finding_rows:
                st.caption(
                    "Confidence is a rule-based estimate of evidence strength, "
                    "not a statistical probability."
                )
                st.dataframe(
                    pd.DataFrame(finding_rows),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.success("No findings detected.")

        with tab2:
            clarification_questions = generate_clarification_questions(findings)
            displayed_questions = 0
            seen_questions = set()
            for priority, questions in clarification_questions.items():
                st.subheader(priority)
                if questions:
                    for question in questions:
                        question_text = re.sub(r"^\[[^]]+\]\s*", "", question)
                        normalized_question = re.sub(
                            r"\s+", " ", question_text
                        ).casefold().strip()
                        if normalized_question in seen_questions:
                            continue
                        seen_questions.add(normalized_question)
                        st.warning(question)
                        displayed_questions += 1
            if not displayed_questions:
                st.write("No clarification questions generated.")

        with tab3:
            answered_questions: list[AlreadyAnsweredQuestion] = result.get(
                "answered_questions",
                [],
            )
            if answered_questions:
                st.caption(
                    "These candidate questions were removed because the uploaded "
                    "document already contained related answer evidence."
                )
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Question removed": answered.question,
                                "Reason": answered.reason,
                                "Answer found": answered.answer,
                                "Source": (
                                    f"{answered.source} — {answered.location}"
                                ),
                            }
                            for answered in answered_questions
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.write("No candidate questions were resolved from document evidence.")

        with tab4:
            acceptance_criteria = format_acceptance_criteria(requirements, findings)
            if acceptance_criteria:
                st.code(acceptance_criteria, language="text")
                if any(finding.generated_scenarios for finding in findings):
                    st.caption(
                        "Suggested scenarios are drafts; confirm them with the "
                        "product owner before treating them as acceptance criteria."
                    )
            else:
                st.info("No existing or suggested acceptance criteria to display.")

        with tab5:
            report = result["report"]
            st.download_button(
                "Download Report",
                data=report,
                file_name="Requirement_Analysis_Report.md",
                mime="text/markdown",
            )
            st.caption(
                "The downloadable report includes the findings, clarification "
                "questions, and suggested acceptance criteria shown in the other tabs."
            )

    st.header("Leadership Metrics")
    if _persistent_history_enabled():
        try:
            history: list[ReviewHistoryEntry] = load_review_history()
        except ReviewHistoryError as exc:
            st.error(f"Could not load review history: {exc}")
            history = []
        if history:
            history_frame = pd.DataFrame(history).rename(
                columns={
                    "document_name": "Document Name",
                    "date": "Date",
                    "score": "Score",
                    "ambiguities": "Ambiguities",
                    "missing_ac": "Missing AC",
                    "conflict": "Conflict",
                    "risk": "Risk",
                    "score_model": "Score Model",
                }
            )
            quality_history = [
                entry for entry in history
                if entry["score_model"] == QUALITY_SCORE_MODEL
            ]
            quality_index, sprint_readiness = st.columns(2)
            if quality_history:
                latest_quality = quality_history[0]
                quality_index.metric(
                    "Requirement Quality Index", f"{latest_quality['score']}%"
                )
                sprint_readiness.metric(
                    "Sprint Readiness",
                    readiness_status_for_score(latest_quality["score"]),
                    delta=f"{latest_quality['score']}%",
                )
            else:
                quality_index.metric("Requirement Quality Index", "Not available")
                sprint_readiness.metric("Sprint Readiness", "Not available")
            st.caption(
                "Readiness is derived from the Requirement Quality Index: High "
                "Risk below 50, Needs Clarification 50–84, Ready for Development "
                "85–100. Historical scores from the previous severity-weighted "
                "model are retained but excluded from the quality-score trend."
            )
            ambiguity_column, missing_ac_column = st.columns(2)
            ambiguity_column.plotly_chart(
                build_history_trend_chart(
                    history_frame, "Ambiguities", "Ambiguity Trend"
                ),
                use_container_width=True,
            )
            missing_ac_column.plotly_chart(
                build_history_trend_chart(
                    history_frame, "Missing AC", "Missing AC Trend"
                ),
                use_container_width=True,
            )
            st.plotly_chart(
                build_risk_heatmap(history_frame),
                use_container_width=True,
            )
            st.header("Review History")
            st.subheader("Saved Review Scores")
            st.dataframe(
                history_frame[["Document Name", "Score", "Score Model"]],
                use_container_width=True,
                hide_index=True,
            )
            if quality_history:
                quality_history_frame = pd.DataFrame(quality_history).rename(
                    columns={
                        "document_name": "Document Name",
                        "date": "Date",
                        "score": "Score",
                    }
                )
                st.plotly_chart(
                    build_history_trend_chart(
                        quality_history_frame,
                        "Score",
                        "Requirement Quality Trend",
                    ),
                    use_container_width=True,
                )
            else:
                st.info("No reviews with the current quality-scoring model yet.")
        else:
            st.info("Complete a review to populate leadership metrics and trends.")
            st.header("Review History")
            st.info("No review runs have been saved yet.")
    else:
        st.info(
            "Persistent review history is disabled for this shared app instance. "
            "This prevents document names and scores from being shared across visitors."
        )


if __name__ == "__main__":
    main()
