"""Final QA gate for consolidated requirement-review findings."""

from collections.abc import Sequence
from dataclasses import replace

from agents.common import Finding, Requirement
from agents.document_reconciliation import reconcile_findings

_SEVERITIES = {"Critical", "High", "Medium", "Low"}


class SeniorQAReviewAgent:
    """Validate final findings for traceability, clarity, and residual duplicates."""

    def review(
        self,
        findings: Sequence[Finding],
        requirements: Sequence[Requirement],
    ) -> list[Finding]:
        requirement_signatures = {
            self._signature(requirement) for requirement in requirements
        }
        for finding in findings:
            if not finding.category.strip():
                raise ValueError("A final finding must have a category.")
            if finding.severity not in _SEVERITIES:
                raise ValueError(
                    f"Final finding '{finding.category}' has unsupported severity "
                    f"'{finding.severity}'."
                )
            if not finding.reason.strip() or not finding.recommendation.strip():
                raise ValueError(
                    f"Final finding '{finding.category}' must include an explanation "
                    "and recommendation."
                )
            if (
                finding.clarification_question is None
                or not finding.clarification_question.strip()
            ):
                raise ValueError(
                    f"Final finding '{finding.category}' has no owner question from "
                    "the clarification stage."
                )
            if finding.requirement is None:
                if not finding.document_scope:
                    raise ValueError(
                        f"Document-level finding '{finding.category}' has no source scope."
                    )
            elif self._signature(finding.requirement) not in requirement_signatures:
                raise ValueError(
                    f"Finding '{finding.category}' references a requirement outside "
                    "the uploaded review batch."
                )

            for related in (
                *((finding.related_requirement,) if finding.related_requirement else ()),
                *finding.supporting_requirements,
            ):
                if self._signature(related) not in requirement_signatures:
                    raise ValueError(
                        f"Finding '{finding.category}' contains supporting evidence "
                        "outside the uploaded review batch."
                    )
        verified = reconcile_findings(findings, requirements)
        return [
            replace(finding, confidence_score=self._confidence_score(finding))
            for finding in verified
        ]

    @staticmethod
    def _confidence_score(finding: Finding) -> int:
        if finding.category == "Conflicting business rules":
            if "cannot both be true" in finding.reason.casefold():
                return 95
            if "inconsistent thresholds" in finding.reason.casefold():
                return 78
            return 88
        if finding.category == "Ambiguous requirement":
            return 88
        if finding.category == "Missing acceptance criteria":
            return 90
        if finding.requirement is None:
            return 70
        return 82

    @staticmethod
    def _signature(requirement: Requirement) -> tuple[str, str, str, str]:
        return (
            requirement.source_path or requirement.source,
            requirement.identifier,
            requirement.location,
            requirement.text,
        )
