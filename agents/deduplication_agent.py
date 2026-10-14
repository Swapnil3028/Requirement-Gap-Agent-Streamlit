"""Consolidate findings that describe the same underlying requirement gap."""

import re
from collections.abc import Sequence

from agents.common import Finding, Requirement


_SEVERITY_RANK = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}
_TOKEN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP_WORDS = {
    "a", "an", "and", "as", "be", "by", "for", "from", "in", "is", "it",
    "of", "on", "or", "shall", "should", "system", "the", "to", "user",
    "users", "must", "can", "will",
}
_RELATED_CATEGORIES = {
    frozenset(("Missing acceptance criteria", "Missing success criteria")),
}


def _requirements(finding: Finding) -> tuple[Requirement, ...]:
    items = []
    if finding.requirement is not None:
        items.append(finding.requirement)
    if finding.related_requirement is not None:
        items.append(finding.related_requirement)
    items.extend(finding.supporting_requirements)
    return tuple(dict.fromkeys(items))


def _category_group(category: str) -> str:
    for pair in _RELATED_CATEGORIES:
        if category in pair:
            return "Missing outcome criteria"
    return category


def _normalized_text(requirement: Requirement) -> str:
    return " ".join(_TOKEN.findall(requirement.text.casefold()))


def _same_underlying_issue(first: Finding, second: Finding) -> bool:
    first_categories = {first.category, *first.merged_categories}
    second_categories = {second.category, *second.merged_categories}
    if not {
        _category_group(category) for category in first_categories
    }.intersection(
        _category_group(category) for category in second_categories
    ):
        return False
    first_requirements = _requirements(first)
    second_requirements = _requirements(second)
    if first.requirement is None or second.requirement is None:
        return (
            first.requirement is None
            and second.requirement is None
            and first.category == second.category
            and first.document_scope == second.document_scope
        )
    first_keys = {
        _normalized_text(item)
        for item in first_requirements
    }
    second_keys = {
        _normalized_text(item)
        for item in second_requirements
    }
    if first.category == second.category == "Conflicting business rules":
        return first_keys == second_keys
    if first_keys.intersection(second_keys):
        return True
    if len(first_requirements) != 1 or len(second_requirements) != 1:
        return False
    first_source = first.requirement.source_path or first.requirement.source
    second_source = second.requirement.source_path or second.requirement.source
    if first_source != second_source:
        return False
    first_tokens = {
        token
        for token in _TOKEN.findall(first.requirement.text.casefold())
        if token not in _STOP_WORDS
    }
    second_tokens = {
        token
        for token in _TOKEN.findall(second.requirement.text.casefold())
        if token not in _STOP_WORDS
    }
    if not first_tokens or not second_tokens:
        return False
    return len(first_tokens & second_tokens) / len(first_tokens | second_tokens) >= 0.9


def _strongest(findings: Sequence[Finding]) -> Finding:
    category_rank = {
        "Missing acceptance criteria": 2,
        "Missing success criteria": 1,
    }
    winner = max(
        findings,
        key=lambda finding: (
            _SEVERITY_RANK.get(finding.severity, 0),
            category_rank.get(finding.category, 0),
            bool(finding.generated_scenarios),
            bool(finding.clarification_question),
            len(finding.reason),
        ),
    )
    evidence = list(_requirements(winner))
    for finding in findings:
        for requirement in _requirements(finding):
            if requirement not in evidence:
                evidence.append(requirement)
    merged_categories = tuple(
        dict.fromkeys(
            category
            for finding in findings
            for category in (finding.category, *finding.merged_categories)
            if category != winner.category
        )
    )
    return Finding(
        category=winner.category,
        severity=winner.severity,
        requirement=winner.requirement,
        reason=winner.reason,
        recommendation=winner.recommendation,
        related_requirement=winner.related_requirement,
        clarification_question=winner.clarification_question,
        generated_scenarios=winner.generated_scenarios,
        document_scope=winner.document_scope,
        supporting_requirements=tuple(
            item for item in evidence
            if item != winner.requirement and item != winner.related_requirement
        ),
        merged_categories=merged_categories,
    )


class DeduplicationAgent:
    """Deduplicate and merge findings, retaining the strongest version."""

    def consolidate(self, findings: Sequence[Finding]) -> list[Finding]:
        consolidated: list[Finding] = []
        for finding in findings:
            for index, existing in enumerate(consolidated):
                if _same_underlying_issue(existing, finding):
                    consolidated[index] = _strongest((existing, finding))
                    break
            else:
                consolidated.append(_strongest((finding,)))
        return consolidated
