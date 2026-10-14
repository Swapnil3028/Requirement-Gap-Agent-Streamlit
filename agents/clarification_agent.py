"""Generate stakeholder questions from requirement review findings."""

from dataclasses import replace
from collections.abc import Sequence

from agents.common import Finding

PRIORITY_ORDER = ("Critical", "High", "Medium", "Low")


def generate_clarification_question(finding: Finding) -> str:
    """Turn a review finding into a focused question for the requirement owner."""
    if finding.requirement is None:
        return finding.clarification_question or (
            f"For the uploaded document batch ({finding.document_scope or 'unknown source'}), "
            f"what decision is needed for {finding.category}?"
        )
    requirement = finding.requirement.text
    if finding.clarification_question and finding.category.startswith(
        ("Missing NFR:", "Missing business rule:")
    ):
        return finding.clarification_question
    if finding.category == "Ambiguous requirement":
        return finding.clarification_question or (
            f"What measurable threshold or observable condition is intended by "
            f"the wording in “{requirement}”?"
        )
    if finding.category == "Missing acceptance criteria":
        return (
            f"For “{requirement}”, which acceptance-test scenarios should be "
            "covered, and what outcome should each assert where the uploaded "
            "document does not already state it?"
        )
    if finding.category == "Conflicting business rules":
        related = (
            finding.related_requirement.text
            if finding.related_requirement is not None
            else "No second statement is available in the uploaded requirements."
        )
        return (
            f"The uploaded statements are “{requirement}” and “{related}”. "
            "Which statement is authoritative, or should both be revised?"
        )
    return (
        f"For “{requirement}”, what observable result or measurable threshold "
        "should be used as the pass/fail oracle?"
    )


def _priority_for_finding(finding: Finding) -> str:
    severity = finding.severity.capitalize()
    return severity if severity in PRIORITY_ORDER else "Medium"


def _question_for_group(findings: Sequence[Finding]) -> str:
    """Combine repeated question templates while retaining every cited statement."""
    first = findings[0]
    if first.requirement is None:
        return "; ".join(
            finding.clarification_question
            or f"What decision is needed for {finding.category}?"
            for finding in findings
        )
    requirements = []
    seen_requirements = set()
    for finding in findings:
        requirement = finding.requirement
        if requirement is None:
            continue
        signature = (requirement.location, requirement.text)
        if signature not in seen_requirements:
            seen_requirements.add(signature)
            requirements.append(
                f"{requirement.location}: “{requirement.text}”"
            )

    evidence = "; ".join(requirements)
    if first.category == "Missing acceptance criteria":
        return (
            f"For these requirements in {first.requirement.source}: {evidence}, "
            "which acceptance-test scenarios should be covered for each behavior, "
            "and what outcome should each assert where the uploaded document does "
            "not already state it?"
        )
    if first.category == "Ambiguous requirement":
        return (
            f"For these requirements in {first.requirement.source}: {evidence}, "
            "what measurable threshold or observable rule is intended for the "
            "flagged wording in each?"
        )
    if first.category == "Testability gap":
        return (
            f"For these requirements in {first.requirement.source}: {evidence}, "
            "what observable result or measurable threshold should be used as "
            "the pass/fail oracle for each?"
        )

    statements = []
    seen_pairs = set()
    for finding in findings:
        if finding.requirement is None:
            pair = (
                finding.category,
                finding.document_scope,
            )
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                statements.append(
                    finding.clarification_question
                    or f"Decision needed for {finding.category}."
                )
            continue
        pair = (
            finding.requirement.text,
            finding.related_requirement.text
            if finding.related_requirement is not None
            else None,
        )
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            related = (
                f" and “{pair[1]}”"
                if pair[1] is not None
                else ""
            )
            statements.append(f"“{pair[0]}”{related}")
    return (
        f"Which statements are authoritative for these conflicts in "
        f"{first.requirement.source}: {'; '.join(statements)}?"
    )


def generate_clarification_questions(
    findings: Sequence[Finding],
) -> dict[str, list[str]]:
    """Group repeated Product Owner question templates by source and priority."""
    grouped: dict[str, list[str]] = {priority: [] for priority in PRIORITY_ORDER}
    question_groups: dict[tuple[str, str, str], list[Finding]] = {}
    for finding in findings:
        priority = _priority_for_finding(finding)
        source = (
            finding.requirement.source_path or finding.requirement.source
            if finding.requirement is not None
            else finding.document_scope or "uploaded document batch"
        )
        key = (priority, finding.category, source.casefold())
        question_groups.setdefault(key, []).append(finding)

    for (priority, _, _), findings_in_group in question_groups.items():
        if len(findings_in_group) == 1 and findings_in_group[0].requirement is not None:
            finding = findings_in_group[0]
            source = f"{finding.requirement.source}, {finding.requirement.location}"
            question = generate_clarification_question(finding)
            grouped[priority].append(f"[{source}] {question}")
        else:
            first_requirement = findings_in_group[0].requirement
            source = (
                first_requirement.source
                if first_requirement is not None
                else findings_in_group[0].document_scope or "uploaded document batch"
            )
            locations = ", ".join(
                dict.fromkeys(
                    finding.requirement.location
                    for finding in findings_in_group
                    if finding.requirement is not None
                )
            )
            grouped[priority].append(
                f"[{source}{f', {locations}' if locations else ''}] "
                f"{_question_for_group(findings_in_group)}"
            )
    return grouped


class ClarificationAgent:
    """Attach an actionable owner question to each initial finding."""

    def review(self, findings: Sequence[Finding]) -> list[Finding]:
        return [
            replace(
                finding,
                clarification_question=(
                    finding.clarification_question
                    or generate_clarification_question(finding)
                ),
            )
            for finding in findings
        ]