"""Reconcile review findings against evidence elsewhere in the upload batch."""

import re
from collections.abc import Sequence

from agents.acceptance_agent import has_acceptance_criteria
from agents.ambiguity_agent import VAGUE_TERMS
from agents.common import Finding, Requirement


_TOKEN = re.compile(r"[a-z][a-z0-9]+", re.IGNORECASE)
_MEASURABLE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:%|milliseconds?|ms|seconds?|minutes?|hours?|days?|"
    r"weeks?|months?|years?|items?|records?|users?|requests?|"
    r"bytes?|KB|MB|GB|TB)\b"
    r"|\b(?:at least|at most|no more than|no less than|less than|greater than|"
    r"within|between|equal to|maximum(?: of)?|minimum(?: of)?|uptime|availability)\b",
    re.IGNORECASE,
)
_OBSERVABLE = re.compile(
    r"\b(?:accepts?|blocks?|creates?|deletes?|denies?|displays?|logs?|rejects?|"
    r"redirects?|returns?|responds?|saves?|sends?|shows?|stores?|updates?|"
    r"receives?|confirms?|allows?|prevents?)\b",
    re.IGNORECASE,
)
_RULE_DETAIL = re.compile(
    r"\b(?:formula|calculation rule|calculated as|computed as|"
    r"percentage of|rate of|sum of|based on|round(?:ed|ing)? to|"
    r"multiply|divid(?:e|ed) by|subtract(?:ed)?|add(?:ed)? to|"
    r"before|after|until|when|once|unless|provided that|"
    r"status|state|transition|step \d+|first|then)\b"
    r"|\b\d+(?:\.\d+)?\s*%",
    re.IGNORECASE,
)
_VALIDATION_DETAIL = re.compile(
    r"\b(?:valid(?:ation)?|invalid|reject\w*|error|message|warn\w*|"
    r"block\w*|prevent\w*|allow\w*|range|format|length)\b",
    re.IGNORECASE,
)
_PRIORITY_EVIDENCE = re.compile(
    r"\b(?:priority\s*[:=]?\s*(?:p[0-3]|critical|high|medium|low)|"
    r"must have|should have|could have|won't have|"
    r"critical|high priority|medium priority|low priority)\b",
    re.IGNORECASE,
)
_STOP_WORDS = {
    "a", "about", "after", "all", "also", "an", "and", "any", "are", "as",
    "at", "be", "because", "before", "being", "between", "both", "but", "by",
    "can", "could", "did", "do", "does", "each", "for", "from", "had", "has",
    "have", "he", "her", "here", "hers", "him", "his", "how", "i", "if", "in",
    "into", "is", "it", "its", "may", "might", "must", "need", "needs", "no",
    "nor", "not", "of", "on", "or", "our", "ours", "shall", "she", "should",
    "so", "some", "such", "than", "that", "the", "their", "them", "then",
    "there", "these", "they", "this", "those", "through", "to", "under", "up",
    "was", "we", "were", "what", "when", "where", "which", "who", "will",
    "with", "would", "you", "your", "system", "application", "user", "users",
    "customer", "customers", "buyer", "admin", "administrator", "as",
    "story", "stories", "acceptance", "criteria", "requirement", "requirements",
}
_VAGUE_TERM_PATTERNS = tuple(
    re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
    for term in VAGUE_TERMS
)
_TOPIC_SYNONYMS = {
    "canceled": "cancel",
    "cancels": "cancel",
    "cancelled": "cancel",
    "cancelling": "cancel",
    "cancellation": "cancel",
    "displayed": "display",
    "displays": "display",
    "displaying": "display",
    "orders": "order",
    "place": "submit",
    "places": "submit",
    "placing": "submit",
    "placed": "submit",
    "submit": "submit",
    "submits": "submit",
    "submitted": "submit",
    "submitting": "submit",
    "views": "view",
    "viewed": "view",
    "viewing": "view",
}


def _topic_terms(text: str) -> set[str]:
    return {
        _TOPIC_SYNONYMS.get(token.lower(), token.lower())
        for token in _TOKEN.findall(text)
        if len(token) > 2 and token.lower() not in _STOP_WORDS
    }


def _related_topic(
    source: Requirement,
    evidence: Requirement,
    minimum_overlap: int = 1,
) -> bool:
    source_terms = _topic_terms(source.text)
    if not source_terms:
        return False
    overlap = source_terms.intersection(_topic_terms(evidence.text))
    return len(overlap) >= min(minimum_overlap, len(source_terms))


def _self_evidence_resolves_ambiguity(requirement: Requirement) -> bool:
    vague_spans = [
        match.span()
        for pattern in _VAGUE_TERM_PATTERNS
        for match in pattern.finditer(requirement.text)
    ]
    measurable_spans = [match.span() for match in _MEASURABLE.finditer(requirement.text)]
    for vague_start, vague_end in vague_spans:
        for measure_start, measure_end in measurable_spans:
            gap_start = min(vague_end, measure_end)
            gap_end = max(vague_start, measure_start)
            if len(_TOKEN.findall(requirement.text[gap_start:gap_end])) <= 8:
                return True
    return False


def _evidence_answers(finding: Finding, evidence: Requirement) -> bool:
    source = finding.requirement
    if source is None:
        return False
    if (
        evidence is source
        and finding.category == "Ambiguous requirement"
    ):
        return _self_evidence_resolves_ambiguity(source)
    if evidence is source:
        return False

    minimum_overlap = 2 if finding.category == "Missing acceptance criteria" else 1
    if not _related_topic(source, evidence, minimum_overlap):
        return False

    if finding.category == "Missing acceptance criteria":
        return (
            bool(evidence.acceptance_criteria and evidence.acceptance_criteria.strip())
            or has_acceptance_criteria(evidence)
            or bool(_OBSERVABLE.search(evidence.text))
        )
    if finding.category == "Ambiguous requirement":
        return bool(_MEASURABLE.search(evidence.text))
    if finding.category == "Testability gap":
        return bool(_OBSERVABLE.search(evidence.text) or _MEASURABLE.search(evidence.text))
    if finding.category.startswith("Missing business rule:"):
        return bool(_RULE_DETAIL.search(evidence.text))
    if finding.category == "Missing validation":
        return bool(_VALIDATION_DETAIL.search(evidence.text))
    if finding.category == "Missing success criteria":
        return bool(_OBSERVABLE.search(evidence.text) or _MEASURABLE.search(evidence.text))
    if finding.category == "Missing prioritization":
        return bool(_PRIORITY_EVIDENCE.search(evidence.text))
    return False


def find_answering_evidence(
    finding: Finding,
    requirements: Sequence[Requirement],
) -> Requirement | None:
    """Return the first in-document statement that resolves a finding."""
    return next(
        (
            evidence
            for evidence in requirements
            if _evidence_answers(finding, evidence)
        ),
        None,
    )


def reconcile_findings(
    findings: Sequence[Finding],
    requirements: Sequence[Requirement],
) -> list[Finding]:
    """Keep only findings not answered by relevant evidence elsewhere in the batch."""
    reconciled = []
    for finding in findings:
        source = finding.requirement
        if source is None:
            reconciled.append(finding)
            continue
        if find_answering_evidence(finding, requirements) is None:
            reconciled.append(finding)
    return reconciled


class DocumentRevalidationAgent:
    """Second-pass reviewer that checks findings against the full upload."""

    def review(
        self,
        findings: Sequence[Finding],
        requirements: Sequence[Requirement],
    ) -> list[Finding]:
        return reconcile_findings(findings, requirements)
