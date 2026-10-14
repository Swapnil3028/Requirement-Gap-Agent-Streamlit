"""Review software requirements and write a Markdown gap report."""

import argparse
import re
import sys
from itertools import chain
from pathlib import Path
from typing import Sequence

from agents.acceptance_agent import detect_missing_acceptance_criteria
from agents.already_answered_agent import (
    AlreadyAnsweredQuestion,
    WasThisAlreadyAnsweredAgent,
)
from agents.ambiguity_agent import detect_ambiguity
from agents.clarification_agent import (
    ClarificationAgent,
    generate_clarification_question,
    generate_clarification_questions,
)
from agents.common import Finding, Requirement
from agents.domain_detection import (
    BusinessDomainAssessment,
    detect_business_domain,
)
from agents.finding_presentation import (
    finding_category_display_title,
    finding_display_title,
)
from agents.human_tone_agent import HumanToneRewriteAgent, HumanizedFinding
from agents.completeness_agent import (
    detect_missing_nfrs,
    detect_missing_success_criteria,
    detect_missing_validation,
    detect_undefined_business_rules,
)
from agents.conflict_agent import detect_conflicts
from agents.deduplication_agent import DeduplicationAgent
from agents.senior_qa_agent import SeniorQAReviewAgent
from agents.quality_score import assess_requirement_quality
from agents.quality_characteristics import (
    assess_quality_characteristics,
    detect_modifiability_concerns,
    detect_prioritization_gaps,
)
from agents.testability_agent import detect_testability_gaps
from document_parser import DocumentParserError, extract_text
from rag_store import (
    RequirementRAG,
    RequirementRAGError,
    RetrievedSection,
    requirement_key,
)


_HEADER_NAMES = {
    "acceptance criteria",
    "description",
    "id",
    "requirement",
    "requirement id",
    "story",
    "story id",
    "title",
    "user story",
}
_AC_HEADERS = {"acceptance criteria", "acceptance criteria (ac)"}


class InputError(Exception):
    """Raised when an input document cannot be reviewed."""


def _normalize_header(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower().replace("_", " "))


def _is_header_row(values: list[object]) -> bool:
    headers = {_normalize_header(value) for value in values if value is not None}
    return len(headers.intersection(_HEADER_NAMES)) >= 2


def _make_requirement(
    text: str,
    source: Path,
    location: str,
    index: int,
    acceptance_criteria: str | None = None,
) -> Requirement:
    return Requirement(
        identifier=f"{source.name}:{index}",
        source=source.name,
        location=location,
        text=text.strip(),
        acceptance_criteria=acceptance_criteria,
        source_path=str(source.resolve()),
    )


def _read_text(path: Path) -> list[Requirement]:
    return _requirements_from_text(extract_text(path), path)


def _requirements_from_text(contents: str, path: Path) -> list[Requirement]:
    """Convert document paragraphs into meaningful requirement statements."""
    raw_paragraphs = [
        [line.strip() for line in paragraph.splitlines() if line.strip()]
        for paragraph in re.split(r"\n\s*\n+", contents)
    ]

    def is_structure_label(text: str) -> bool:
        return bool(
            re.match(
                r"^(?:feature|rule|background|scenario(?: outline)?(?:\s+\d+)?|examples?)"
                r"\s*:",
                text,
                re.IGNORECASE,
            )
        )

    def is_heading(text: str) -> bool:
        normalized = re.sub(r"^[\s#>*-]+", "", text).strip()
        words = re.findall(r"\b[\w'-]+\b", normalized)
        if len(words) < 2:
            return True
        if re.fullmatch(
            r"(?:contents|overview|introduction|purpose|scope|"
            r"functional requirements|non-functional requirements|"
            r"business requirements|business rules|acceptance criteria|"
            r"definition of done|user stories|requirements)",
            normalized.rstrip(":").strip(),
            re.IGNORECASE,
        ):
            return True
        has_requirement_language = re.search(
            r"\b(?:shall|should|must|can|may|will|need(?:s)? to|"
            r"require[sd]?|allow(?:s|ed)?|display(?:s|ed)?|show(?:s|n)?|"
            r"save[sd]?|send[sd]?|return[sd]?|provide[sd]?|"
            r"create[sd]?|update[sd]?|delete[sd]?|process(?:es|ed)?|"
            r"calculate[sd]?|appear[sd]?|is|are|has|have|"
            r"given|when|then)\b",
            normalized,
            re.IGNORECASE,
        )
        title_case = all(
            word[0].isupper() for word in words if word[0].isalpha()
        )
        numbered_heading = bool(
            re.match(r"^\d+(?:\.\d+)*\s+[A-Z]", normalized)
        )
        return (
            not has_requirement_language
            and not re.search(r"[.!?]", normalized)
            and (title_case or numbered_heading)
        )

    paragraphs: list[str] = []
    for lines in raw_paragraphs:
        if not lines:
            continue
        if path.suffix.lower() == ".pdf":
            current_lines = []
            for line in lines:
                if is_heading(line) or is_structure_label(line):
                    if current_lines:
                        paragraphs.append(" ".join(current_lines))
                        current_lines = []
                    continue
                current_lines.append(line)
                if re.search(r"""[.!?]["')\]]*$""", line):
                    paragraphs.append(" ".join(current_lines))
                    current_lines = []
            if current_lines:
                paragraphs.append(" ".join(current_lines))
        else:
            paragraphs.append(" ".join(lines))

    statements: list[str] = []
    scenario_steps: list[str] = []
    for paragraph in paragraphs:
        if is_structure_label(paragraph):
            if scenario_steps:
                statements.append(" ".join(scenario_steps))
                scenario_steps.clear()
            continue

        if re.match(r"^(?:given|when|then)\b", paragraph, re.IGNORECASE):
            scenario_steps.append(paragraph)
            if re.search(r"\bthen\b", " ".join(scenario_steps), re.IGNORECASE):
                statements.append(" ".join(scenario_steps))
                scenario_steps.clear()
            continue

        if scenario_steps:
            statements.append(" ".join(scenario_steps))
            scenario_steps.clear()
        if not is_heading(paragraph):
            statements.append(paragraph)
    if scenario_steps:
        statements.append(" ".join(scenario_steps))

    requirements = []
    for text in statements:
        if not text:
            continue
        requirements.append(
            _make_requirement(
                text,
                path,
                f"paragraph {len(requirements) + 1}",
                len(requirements) + 1,
            )
        )
    return requirements


def _read_docx(path: Path) -> list[Requirement]:
    return _requirements_from_text(extract_text(path), path)


def _read_xlsx(path: Path) -> list[Requirement]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise InputError(
            "XLSX support is missing. Install dependencies with: "
            "python -m pip install -r requirements.txt"
        ) from exc
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise InputError(f"Could not read XLSX file '{path}': {exc}") from exc

    requirements = []
    try:
        for worksheet in workbook.worksheets:
            rows = worksheet.iter_rows(values_only=True)
            first_row = next(rows, None)
            if first_row is None:
                continue
            header_values = list(first_row)
            headers = header_values if _is_header_row(header_values) else None
            data_rows = rows if headers is not None else chain([first_row], rows)
            start_row = 2 if headers is not None else 1
            for row_number, row in enumerate(data_rows, start_row):
                values = list(row)
                if headers is None:
                    pairs = [
                        (f"column {index}", value)
                        for index, value in enumerate(values, 1)
                    ]
                    acceptance_criteria = None
                else:
                    pairs = [
                        (str(headers[index]).strip(), value)
                        for index, value in enumerate(values)
                        if index < len(headers) and headers[index] is not None
                    ]
                    acceptance_criteria = next(
                        (
                            str(value).strip()
                            for header, value in pairs
                            if _normalize_header(header) in _AC_HEADERS
                            and value is not None
                        ),
                        None,
                    )
                text_parts = [
                    f"{header}: {str(value).strip()}"
                    for header, value in pairs
                    if value is not None and str(value).strip()
                ]
                text = " | ".join(text_parts)
                if text:
                    requirements.append(
                        _make_requirement(
                            text,
                            path,
                            f"sheet '{worksheet.title}', row {row_number}",
                            len(requirements) + 1,
                            acceptance_criteria,
                        )
                    )
    finally:
        workbook.close()
    return requirements


def load_requirements(path: Path) -> list[Requirement]:
    """Load requirements from DOCX, XLSX, Markdown, or plain text."""
    if not path.exists():
        raise InputError(f"Input file does not exist: {path}")
    if not path.is_file():
        raise InputError(f"Input path is not a file: {path}")
    if path.stat().st_size == 0:
        raise InputError(f"Input file is empty: {path}")

    suffix = path.suffix.lower()
    try:
        if suffix in {".md", ".txt"}:
            requirements = _read_text(path)
        elif suffix in {".docx", ".pdf"}:
            requirements = _requirements_from_text(extract_text(path), path)
        elif suffix == ".xlsx":
            requirements = _read_xlsx(path)
        else:
            raise InputError(
                f"Unsupported input format '{suffix}' for '{path}'. "
                "Use .pdf, .docx, .xlsx, .md, or .txt."
            )
    except DocumentParserError as exc:
        raise InputError(str(exc)) from exc
    if not requirements:
        raise InputError(f"No non-empty requirement statements found in: {path}")
    return requirements


def review_requirements(
    requirements: Sequence[Requirement],
    retrieved_sections: dict[str, list[RetrievedSection]] | None = None,
) -> list[Finding]:
    """Run the requirement checks and return only unresolved findings."""
    findings, _, _ = review_requirements_with_context(requirements, retrieved_sections)
    return findings


def review_requirements_with_audit(
    requirements: Sequence[Requirement],
    retrieved_sections: dict[str, list[RetrievedSection]] | None = None,
) -> tuple[list[Finding], list[AlreadyAnsweredQuestion]]:
    """Run the review and return unresolved findings plus answered questions."""
    findings, answered_questions, _ = review_requirements_with_context(
        requirements,
        retrieved_sections,
    )
    return findings, answered_questions


def review_requirements_with_context(
    requirements: Sequence[Requirement],
    retrieved_sections: dict[str, list[RetrievedSection]] | None = None,
) -> tuple[
    list[Finding],
    list[AlreadyAnsweredQuestion],
    BusinessDomainAssessment,
]:
    """Detect the domain first, then perform document-wide requirement checks."""
    domain_assessment = detect_business_domain(requirements)
    if retrieved_sections:
        retrieved_sections = restrict_retrieved_sections(requirements, retrieved_sections)
    findings = detect_ambiguity(requirements)
    findings.extend(detect_missing_acceptance_criteria(requirements))
    findings.extend(detect_testability_gaps(requirements))
    findings.extend(detect_conflicts(requirements))
    if retrieved_sections:
        current_keys = {requirement_key(requirement) for requirement in requirements}
        compared_pairs = {
            frozenset(
                (
                    requirement_key(finding.requirement),
                    requirement_key(finding.related_requirement),
                )
            )
            for finding in findings
            if finding.category == "Conflicting business rules"
            and finding.requirement is not None
            and finding.related_requirement is not None
        }
        for requirement in requirements:
            for section in retrieved_sections.get(requirement_key(requirement), []):
                related = section.requirement
                related_key = requirement_key(related)
                if related_key in current_keys:
                    continue
                pair_key = frozenset((requirement_key(requirement), related_key))
                if pair_key in compared_pairs:
                    continue
                related_findings = detect_conflicts([requirement, related])
                findings.extend(related_findings)
                if related_findings:
                    compared_pairs.add(pair_key)
    findings.extend(detect_undefined_business_rules(requirements))
    findings.extend(detect_missing_validation(requirements))
    findings.extend(detect_missing_success_criteria(requirements))
    findings.extend(detect_missing_nfrs(requirements))
    findings.extend(detect_prioritization_gaps(requirements))
    findings.extend(detect_modifiability_concerns(requirements))

    findings = ClarificationAgent().review(findings)
    findings = DeduplicationAgent().consolidate(findings)
    findings, answered_questions = WasThisAlreadyAnsweredAgent().review(
        findings,
        requirements,
    )
    findings = SeniorQAReviewAgent().review(findings, requirements)
    return findings, answered_questions, domain_assessment


def restrict_retrieved_sections(
    requirements: Sequence[Requirement],
    retrieved_sections: dict[str, list[RetrievedSection]],
) -> dict[str, list[RetrievedSection]]:
    """Keep retrieved evidence within the documents in the current review batch."""
    def signature(requirement: Requirement) -> tuple[str, str, str, str, str | None]:
        return (
            requirement.source_path or requirement.source,
            requirement.identifier,
            requirement.location,
            requirement.text,
            requirement.acceptance_criteria,
        )

    current_signatures = {signature(requirement) for requirement in requirements}
    return {
        requirement_key(requirement): [
            section
            for section in retrieved_sections.get(requirement_key(requirement), [])
            if signature(section.requirement) in current_signatures
        ]
        for requirement in requirements
    }


def _escape_markdown(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _executive_clarification_label(finding: Finding) -> str:
    """Return a concise stakeholder-facing label for an open review area."""
    category = finding.category
    if category.startswith(("Missing NFR:", "Incomplete NFR:")):
        area = category.partition(":")[2].strip()
        return f"{area} targets"
    if category.startswith("Missing business rule:"):
        detail = category.partition(":")[2].strip()
        return "Calculation rules" if detail == "calculation" else "Business workflow rules"
    labels = {
        "Ambiguous requirement": "Requirement wording and thresholds",
        "Missing acceptance criteria": "Acceptance criteria",
        "Conflicting business rules": "Conflicting business rules",
        "Testability gap": "Observable outcomes and pass/fail criteria",
        "Missing validation": "Validation behavior",
        "Missing success criteria": "Success criteria",
        "Missing prioritization": "Requirement priorities",
        "Modifiability concern": "Requirement scope and separation",
    }
    return labels.get(category, finding_display_title(finding))


def render_report(
    requirements: Sequence[Requirement],
    findings: Sequence[Finding],
    retrieved_sections: dict[str, list[RetrievedSection]] | None = None,
    answered_questions: Sequence[AlreadyAnsweredQuestion] = (),
    domain_assessment: BusinessDomainAssessment | None = None,
    humanized_findings: Sequence[HumanizedFinding] | None = None,
) -> str:
    """Render a complete Markdown report for the reviewed requirements."""
    if domain_assessment is None:
        domain_assessment = detect_business_domain(requirements)
    if retrieved_sections:
        retrieved_sections = restrict_retrieved_sections(requirements, retrieved_sections)
    if humanized_findings is None:
        humanized_findings = HumanToneRewriteAgent().rewrite_all(findings)
    elif len(humanized_findings) != len(findings) or any(
        presentation.finding is not finding
        for presentation, finding in zip(humanized_findings, findings)
    ):
        raise ValueError("Humanized findings must match the reviewed findings in order.")
    presentation_by_finding_id = {
        id(presentation.finding): presentation for presentation in humanized_findings
    }
    categories = (
        "Ambiguous requirement",
        "Missing acceptance criteria",
        "Conflicting business rules",
        "Testability gap",
    )
    dynamic_categories = sorted(
        {
            category
            for finding in findings
            for category in (finding.category, *finding.merged_categories)
            if category.startswith(
                (
                    "Missing business rule:",
                    "Missing NFR:",
                    "Incomplete NFR:",
                    "Missing validation",
                    "Missing success criteria",
                    "Missing prioritization",
                    "Modifiability concern",
                )
            )
        }
    )
    categories = (*categories, *dynamic_categories)
    assessment = assess_requirement_quality(findings)
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    prioritized_findings = sorted(
        findings,
        key=lambda finding: (
            severity_rank.get(finding.severity.casefold(), len(severity_rank)),
            -finding.confidence_score,
            _executive_clarification_label(finding).casefold(),
        ),
    )
    clarification_areas: list[str] = []
    for finding in prioritized_findings:
        label = _executive_clarification_label(finding)
        if label not in clarification_areas:
            clarification_areas.append(label)
    executive_areas = clarification_areas[:5]

    def count_category(category: str) -> int:
        return sum(
            finding.category == category or category in finding.merged_categories
            for finding in findings
        )

    if domain_assessment.domain == "Undetermined":
        domain_label = "Undetermined"
        if domain_assessment.alternative_domains:
            domain_label += (
                " (possible matches: "
                + ", ".join(domain_assessment.alternative_domains)
                + ")"
            )
    else:
        domain_label = (
            f"{domain_assessment.domain} "
            f"({domain_assessment.confidence}% heuristic confidence)"
        )

    lines = [
        "# Requirement Gap Review",
        "",
        f"- Requirements reviewed: **{len(requirements)}**",
        f"- Findings: **{len(findings)}**",
        f"- Requirement quality score: **{assessment.score}/100**",
        f"- Readiness status: **{assessment.readiness_status}**",
        "",
        "## Business Domain Context",
        "",
        f"- **Likely domain:** {domain_label}",
        "- **Evidence:** "
        + (", ".join(domain_assessment.evidence) or "No distinctive domain terms found."),
        "- The following are domain-informed review prompts, not findings or "
        "assumed requirements. Confirm applicability with stakeholders.",
        *[f"- {question}" for question in domain_assessment.review_questions],
        "",
        "- Review pipeline: parser -> business-domain detection -> ambiguity -> "
        "acceptance criteria -> "
        "business rules -> NFR -> clarification -> deduplication -> "
        "already-answered question review -> senior QA and confidence scoring -> "
        "human tone rewrite -> final report.",
        "- Cross-reference scope: all extracted BRD sections, business rules, "
        "appendices, workflows, acceptance criteria, and uploaded documents.",
        "",
        "## Requirement Quality Review",
        "",
        f"Ambiguous requirements: **{assessment.ambiguous_requirements}**. "
        f"Testability gaps: **{sum(f.category == 'Testability gap' for f in findings)}**.",
        "",
        "## Acceptance Criteria Review",
        "",
        f"Requirements missing acceptance criteria: **{assessment.missing_acceptance_criteria}**. "
        + (
            "No missing acceptance-criteria findings."
            if assessment.missing_acceptance_criteria == 0
            else ""
        )
        + " "
        "Draft positive, negative, validation, exception, and boundary scenarios "
        "are included only when an explicit user-story actor and action can be "
        "identified; unresolved expected outcomes remain Product Owner placeholders.",
        "",
        "## Business Rule Analysis",
        "",
        f"Missing business-rule findings: **{assessment.missing_business_rules}**. "
        f"Conflicting business rules: **{assessment.conflicting_rules}**. "
        + (
            "No missing business-rule findings."
            if assessment.missing_business_rules == 0
            else ""
        )
        + (
            " No conflicting rules detected."
            if assessment.conflicting_rules == 0
            else ""
        ),
        "",
        "## Testability Review",
        "",
        "Testability findings are listed under **Testability gap** below.",
        "",
        "## Non-Functional Requirement Review",
        "",
        f"Document-level NFR gaps (missing or lacking area-specific detail): "
        f"**{assessment.missing_nfrs}**. "
        + (
            "No NFR coverage omissions were detected by the configured keyword checks. "
            if assessment.missing_nfrs == 0
            else ""
        )
        + "These coverage prompts do not assert that the system lacks an attribute.",
        "",
        "## Edge Case Review",
        "",
        "When a recognizable user story lacks acceptance criteria, the draft "
        "scenario categories are listed with unresolved behavior explicitly "
        "reserved for Product Owner input.",
        "",
        "## Overall Readiness and Quality Score",
        "",
        f"Score: **{assessment.score}/100** — **{assessment.readiness_status}**.",
        "Deductions: 10 per ambiguous requirement; 10 per missing acceptance "
        "criteria; 5 per missing business rule; 5 per missing NFR area; 15 per "
        "conflict. Score is clamped to 0–100. Readiness: High Risk below 50, "
        "Needs Clarification 50–84, Ready for Development 85–100.",
        "",
        "## Summary",
        "",
        "| Review area | Findings |",
        "| --- | ---: |",
    ]
    for category in categories:
        count = count_category(category)
        lines.append(f"| {_escape_markdown(category)} | {count} |")

    lines.extend(
        (
            "",
            "## Quality Characteristic Checks",
            "",
            "| Characteristic | Status | Review evidence |",
            "| --- | --- | --- |",
        )
    )
    for check in assess_quality_characteristics(requirements, findings):
        lines.append(
            f"| {_escape_markdown(check.name)} | "
            f"{_escape_markdown(check.status)} | "
            f"{_escape_markdown(check.evidence)} |"
        )
    lines.extend(
        (
            "",
            f"Requirements missing validation outcomes: "
            f"**{count_category('Missing validation')}**.",
            f"Requirements missing explicit success criteria: "
            f"**{count_category('Missing success criteria')}**.",
        )
    )

    for category in categories:
        category_findings = [finding for finding in findings if finding.category == category]
        section_title = (
            finding_display_title(category_findings[0])
            if category_findings
            else finding_category_display_title(category)
        )
        lines.extend(("", f"## {section_title} ({len(category_findings)})", ""))
        if not category_findings:
            consolidated_count = count_category(category)
            if consolidated_count:
                lines.append(
                    f"{consolidated_count} issue(s) consolidated under a stronger "
                    "finding category above."
                )
            else:
                lines.append("No issues detected.")
            continue
        for index, finding in enumerate(category_findings, 1):
            presentation = presentation_by_finding_id[id(finding)]
            requirement = finding.requirement
            if requirement is None:
                source = finding.document_scope or "uploaded document batch"
                location = "document-level coverage"
                requirement_text = (
                    "No source requirement identified for this document-level gap."
                )
            else:
                source = requirement.source
                location = requirement.location
                requirement_text = requirement.text
            lines.extend(
                (
                    f"### {index}. {presentation.title}",
                    "",
                    f"- **{'Conflict severity' if finding.category == 'Conflicting business rules' else 'Severity'}:** "
                    f"{finding.severity}",
                    f"- **Confidence:** {finding.confidence_score}%",
                    f"- **Source:** `{_escape_markdown(source)}` "
                    f"({_escape_markdown(location)})",
                    f"- **Requirement:** {_escape_markdown(requirement_text)}",
                )
            )
            if finding.related_requirement is not None:
                related = finding.related_requirement
                lines.append(
                    f"- **Conflicting statement:** `{_escape_markdown(related.source)}` "
                    f"({_escape_markdown(related.location)}): "
                    f"{_escape_markdown(related.text)}"
                )
            if finding.merged_categories:
                lines.append(
                    "- **Also classified as:** "
                    + ", ".join(
                        f"`{_escape_markdown(category)}`"
                        for category in finding.merged_categories
                    )
                )
            for supporting in finding.supporting_requirements:
                lines.append(
                    f"- **Additional source evidence:** "
                    f"`{_escape_markdown(supporting.source)}` "
                    f"({_escape_markdown(supporting.location)}): "
                    f"{_escape_markdown(supporting.text)}"
                )
            lines.extend(
                (
                    f"- **Rationale:** {_escape_markdown(presentation.rationale)}",
                    f"- **Business impact:** "
                    f"{_escape_markdown(presentation.business_impact)}",
                    f"- **Recommendation:** "
                    f"{_escape_markdown(presentation.recommendation)}",
                    f"- **Clarifying question:** "
                    f"{_escape_markdown(generate_clarification_question(finding))}",
                )
            )
            if finding.generated_scenarios:
                lines.extend(
                    (
                        "",
                        "**Draft scenarios — expected outcome pending Product Owner input:**",
                        "",
                    )
                )
                for scenario_category, scenarios in finding.generated_scenarios.items():
                    lines.append(f"- **{_escape_markdown(scenario_category)}**")
                    lines.extend(
                        f"  - {_escape_markdown(scenario)}" for scenario in scenarios
                    )
            lines.append("")

    edge_case_findings = [
        finding for finding in findings if finding.generated_scenarios
    ]
    if edge_case_findings:
        lines.extend(("### Edge-case scenario coverage", ""))
        for finding in edge_case_findings:
            for scenario_category in (
                "Negative scenarios",
                "Validation scenarios",
                "Exception scenarios",
                "Boundary scenarios",
            ):
                if scenario_category in (finding.generated_scenarios or {}):
                    lines.append(
                        f"- **{_escape_markdown(scenario_category)}** for "
                        f"`{_escape_markdown(finding.requirement.identifier if finding.requirement else 'document-level finding')}`"
                    )
        lines.append("")

    if retrieved_sections:
        lines.extend(("", "## Retrieved Related Sections", ""))
        for requirement in requirements:
            sections = retrieved_sections.get(requirement_key(requirement), [])
            if not sections:
                continue
            lines.extend(
                (
                    f"### {_escape_markdown(requirement.identifier)}",
                    "",
                    f"Query: {_escape_markdown(requirement.text)}",
                    "",
                )
            )
            for section in sections:
                related = section.requirement
                lines.append(
                    f"- **{_escape_markdown(related.source)} — "
                    f"{_escape_markdown(related.location)}, "
                    f"chunk {section.chunk_index + 1}:** "
                    f"{_escape_markdown(related.text)}"
                )
            lines.append("")

    if answered_questions:
        lines.extend(("", "## Questions Already Answered in the Document", ""))
        for answered in answered_questions:
            lines.extend(
                (
                    f"- **Question removed:** {_escape_markdown(answered.question)}",
                    f"  - **Reason:** {_escape_markdown(answered.reason)}",
                    f"  - **Answer found:** {_escape_markdown(answered.answer)}",
                    f"  - **Source:** `{_escape_markdown(answered.source)}` "
                    f"({_escape_markdown(answered.location)})",
                )
            )

    clarification_questions = generate_clarification_questions(findings)
    lines.extend(("", "## Product Owner Clarification Questions", ""))
    if not findings:
        lines.append("No clarification questions generated.")
    else:
        for priority, questions in clarification_questions.items():
            lines.extend((f"### {priority}", ""))
            if questions:
                lines.extend(f"- {_escape_markdown(question)}" for question in questions)
            else:
                lines.append("- None.")
            lines.append("")

    lines.extend(
        (
            "## Method and limitations",
            "",
            "This report is generated by deterministic text heuristics. It flags "
            "configured vague wording, checks for explicit acceptance-criteria "
            "evidence, cross-checks potential answers against related statements "
            "throughout the uploaded batch, checks for calculation and workflow detail, identifies "
            "NFR coverage, finds duplicate "
            "rules and opposite-polarity requirements, compares recognized "
            "numeric conditions, and looks for observable outcomes or measurable "
            "thresholds. A finding "
            "is a review prompt, not proof of a defect; the checks can miss "
            "implicit or differently worded issues. Confidence percentages are "
            "rule-based estimates of evidence strength, not statistical "
            "probabilities. Validate findings with the requirement owner.",
            "",
        )
    )
    executive_summary = [
        "## Executive Summary",
        "",
        (
            f"The review covered **{len(requirements)}** requirement(s). "
            f"The overall requirement quality score is **{assessment.score}/100** "
            f"(**{assessment.readiness_status}**)."
        ),
        (
            "Several areas warrant stakeholder clarification before development. "
            "If left unresolved, they may lead to inconsistent implementation or "
            "testing."
            if executive_areas
            else "The configured review checks did not identify actionable "
            "clarification areas. Stakeholder validation is still recommended."
        ),
        "",
    ]
    if executive_areas:
        executive_summary.extend(
            (
                "**Priority areas for clarification:**",
                "",
                *[
                    f"{index}. {area}"
                    for index, area in enumerate(executive_areas, start=1)
                ],
            )
        )
        if len(clarification_areas) > len(executive_areas):
            executive_summary.extend(
                (
                    "",
                    f"{len(clarification_areas) - len(executive_areas)} additional "
                    "clarification area(s) are detailed in the findings below.",
                )
            )
    lines = [*executive_summary, "", *lines]
    return "\n".join(lines)


def _parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze requirements and write a Markdown gap report."
    )
    parser.add_argument(
        "--brd",
        type=Path,
        help="BRD input (.pdf, .docx, .txt, or .md).",
    )
    parser.add_argument(
        "--stories",
        type=Path,
        help="User story input (.pdf, .docx, .xlsx, .txt, or .md).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent
        / "reports"
        / "RequirementGapReport.md",
        help="Markdown report path.",
    )
    parser.add_argument(
        "--rag-directory",
        type=Path,
        default=Path(__file__).resolve().parent / ".rag" / "chroma",
        help="Directory for the persistent local ChromaDB index.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Related sections to retrieve for each requirement.",
    )
    parser.add_argument("--skip-brd", action="store_true", help="Skip the default BRD input.")
    parser.add_argument(
        "--skip-stories",
        action="store_true",
        help="Skip the default user-story input.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    args = _parse_arguments(argv)
    if args.top_k <= 0:
        print("error: --top-k must be greater than zero", file=sys.stderr)
        return 2
    project_root = Path(__file__).resolve().parent
    requirements = []
    inputs = []
    has_custom_input = args.brd is not None or args.stories is not None
    if not args.skip_brd:
        if args.brd is not None:
            inputs.append(args.brd)
        elif not has_custom_input:
            inputs.append(project_root / "sample_docs" / "BRD.docx")
    if not args.skip_stories:
        if args.stories is not None:
            inputs.append(args.stories)
        elif not has_custom_input:
            inputs.append(project_root / "sample_docs" / "UserStories.xlsx")
    if not inputs:
        print("error: at least one input is required", file=sys.stderr)
        return 2

    rag = None
    error_message = None
    try:
        for input_path in inputs:
            requirements.extend(load_requirements(input_path))
        rag = RequirementRAG(args.rag_directory)
        indexed_sections = rag.index_requirements(requirements)
        retrieved_sections = restrict_retrieved_sections(
            requirements,
            rag.retrieve_for_requirements(requirements, args.top_k),
        )
        findings, answered_questions, domain_assessment = review_requirements_with_context(
            requirements,
            retrieved_sections,
        )
        humanized_findings = HumanToneRewriteAgent().rewrite_all(findings)
        report = render_report(
            requirements,
            findings,
            retrieved_sections,
            answered_questions,
            domain_assessment,
            humanized_findings,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    except (InputError, DocumentParserError, RequirementRAGError, OSError) as exc:
        error_message = str(exc)
    finally:
        if rag is not None:
            try:
                rag.close()
            except RequirementRAGError as exc:
                close_error = str(exc)
                error_message = (
                    f"{error_message}; {close_error}" if error_message else close_error
                )

    if error_message:
        print(f"error: {error_message}", file=sys.stderr)
        return 2

    print(
        f"Reviewed {len(requirements)} requirement(s); "
        f"indexed {indexed_sections} section(s); "
        f"wrote {len(findings)} finding(s) to {args.output}"
    )
    return 0


if __name__ == "__main__":
    if "streamlit" in sys.modules:
        from streamlit_app import main as streamlit_main

        streamlit_main()
    else:
        raise SystemExit(main())