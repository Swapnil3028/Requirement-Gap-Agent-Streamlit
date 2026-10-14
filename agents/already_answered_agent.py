"""Remove findings answered by evidence elsewhere in the full document batch."""

from collections.abc import Sequence
from dataclasses import dataclass

from agents.clarification_agent import generate_clarification_question
from agents.common import Finding, Requirement
from agents.document_reconciliation import find_answering_evidence


@dataclass(frozen=True)
class AlreadyAnsweredQuestion:
    """Audit record for a clarification removed using source evidence."""

    question: str
    answer: str
    source: str
    location: str
    reason: str = "Answer already available."


class WasThisAlreadyAnsweredAgent:
    """Check each generated question against every extracted batch statement."""

    def review(
        self,
        findings: Sequence[Finding],
        requirements: Sequence[Requirement],
    ) -> tuple[list[Finding], list[AlreadyAnsweredQuestion]]:
        unresolved: list[Finding] = []
        answered: list[AlreadyAnsweredQuestion] = []
        for finding in findings:
            evidence = find_answering_evidence(finding, requirements)
            if evidence is None:
                unresolved.append(finding)
                continue

            answered.append(
                AlreadyAnsweredQuestion(
                    question=(
                        finding.clarification_question
                        or generate_clarification_question(finding)
                    ),
                    answer=(
                        f"{evidence.text}\nAcceptance criteria: "
                        f"{evidence.acceptance_criteria}"
                        if evidence.acceptance_criteria
                        else evidence.text
                    ),
                    source=evidence.source,
                    location=evidence.location,
                )
            )
        return unresolved, answered
