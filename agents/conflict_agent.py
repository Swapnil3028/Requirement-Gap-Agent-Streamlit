"""Detect duplicate rules and conflicts between requirement statements."""

import re
from collections.abc import Sequence
from itertools import combinations

from agents.common import Finding, Requirement


_TOKEN = re.compile(r"[a-z0-9]+")
_MODALS = {
    "a",
    "an",
    "are",
    "be",
    "can",
    "could",
    "for",
    "is",
    "may",
    "must",
    "of",
    "shall",
    "should",
    "the",
    "to",
    "will",
    "would",
}
_NEGATIONS = {"cannot", "no", "not", "never"}
_ANTONYMS = {
    "active": ("state", 1),
    "allowed": ("permission", 1),
    "disabled": ("state", -1),
    "enabled": ("state", 1),
    "excluded": ("inclusion", -1),
    "forbidden": ("permission", -1),
    "hidden": ("visibility", -1),
    "inactive": ("state", -1),
    "mandatory": ("necessity", 1),
    "optional": ("necessity", -1),
    "permitted": ("permission", 1),
    "prohibited": ("permission", -1),
    "required": ("necessity", 1),
    "visible": ("visibility", 1),
}
_CONDITION = re.compile(
    r"(?P<operator>minimum|maximum|at least|at most|no less than|no more than|"
    r"greater than|less than|older than|younger than|above|below|under|over|"
    r">=|<=|>|<|equal to|exactly)"
    r"\s+(?:age\s+)?(?:(?:of|is)\s+|[=:]\s*)?(?P<value>\d+(?:\.\d+)?)"
    r"|\b(?P<age_operator>age)\s*(?:must\s+be|is|of|[=:])\s*"
    r"(?P<age_value>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_CONDITION_WORDS = {
    "age",
    "at",
    "above",
    "below",
    "equal",
    "exactly",
    "greater",
    "least",
    "less",
    "maximum",
    "minimum",
    "more",
    "no",
    "older",
    "over",
    "under",
    "younger",
}
_REGISTRATION_WORDS = {"register", "registration", "enroll", "enrollment"}
_USER_WORDS = {"user", "users", "customer", "customers", "person", "people"}
_AGE_CONTEXT_WORDS = {"age", "year", "years", "old"}


def _normalize_polarity(text: str) -> tuple[tuple[str, ...], int]:
    tokens = _TOKEN.findall(text.lower())
    normalized = []
    polarity = 1
    for token in tokens:
        if token in _NEGATIONS:
            polarity *= -1
        elif token in _ANTONYMS:
            canonical, direction = _ANTONYMS[token]
            normalized.append(canonical)
            polarity *= direction
        elif token not in _MODALS:
            normalized.append(token)
    return tuple(normalized), polarity


def _threshold_condition(text: str) -> tuple[str, str, float, bool] | None:
    """Return (subject, bound, value, inclusive) for a recognized numeric rule."""
    tokens = _TOKEN.findall(text.lower())
    for match in _CONDITION.finditer(text):
        if match.group("age_operator"):
            operator = "equal"
            value = float(match.group("age_value"))
            condition_start = match.start("age_operator")
            subject = "age"
        else:
            operator = match.group("operator").lower()
            value = float(match.group("value"))
            condition_start = match.start("operator")
            prefix = text[:condition_start]
            suffix = text[match.end() :]
            nearby = set(_TOKEN.findall(prefix.lower())[-4:] + _TOKEN.findall(suffix.lower())[:4])
            all_tokens = set(tokens)
            if (
                "age" in nearby
                or all_tokens.intersection(_AGE_CONTEXT_WORDS)
                or (
                    all_tokens.intersection(_REGISTRATION_WORDS)
                    and all_tokens.intersection(_USER_WORDS)
                )
            ):
                subject = "age"
            else:
                subject_tokens = [
                    token
                    for token in tokens
                    if token not in _MODALS
                    and token not in _CONDITION_WORDS
                    and not re.fullmatch(r"\d+(?:\.\d+)?", token)
                ]
                subject = " ".join(subject_tokens)

        if operator in {"minimum", "at least", "no less than", ">="}:
            return subject, "lower", value, True
        if operator in {"greater than", "older than", "above", "over", ">"}:
            return subject, "lower", value, False
        if operator in {"maximum", "at most", "no more than", "<="}:
            return subject, "upper", value, True
        if operator in {"less than", "younger than", "below", "under", "<"}:
            return subject, "upper", value, False
        return subject, "equal", value, True
    return None


def _threshold_conflict(
    first: tuple[str, str, float, bool],
    second: tuple[str, str, float, bool],
) -> tuple[bool, bool]:
    """Return (different, disjoint) for two thresholds on the same subject."""
    first_subject, first_bound, first_value, first_inclusive = first
    second_subject, second_bound, second_value, second_inclusive = second
    if first_subject != second_subject:
        return False, False
    if first == second:
        return False, False

    bounds = [condition for condition in (first, second) if condition[1] != "equal"]
    equalities = [condition for condition in (first, second) if condition[1] == "equal"]
    if len(equalities) == 2:
        disjoint = first_value != second_value
    elif equalities:
        equality = equalities[0]
        bound = bounds[0]
        value = equality[2]
        disjoint = (
            value < bound[2] or (value == bound[2] and not bound[3])
            if bound[1] == "lower"
            else value > bound[2] or (value == bound[2] and not bound[3])
        )
    else:
        lowers = [condition for condition in bounds if condition[1] == "lower"]
        uppers = [condition for condition in bounds if condition[1] == "upper"]
        disjoint = False
        if lowers and uppers:
            lower = max(lowers, key=lambda condition: condition[2])
            upper = min(uppers, key=lambda condition: condition[2])
            disjoint = lower[2] > upper[2] or (
                lower[2] == upper[2] and not (lower[3] and upper[3])
            )

    return True, disjoint


def detect_conflicts(requirements: Sequence[Requirement]) -> list[Finding]:
    """Compare requirements for contradictions and threshold mismatches."""
    findings = []
    for first, second in combinations(requirements, 2):
        first_core, first_polarity = _normalize_polarity(first.text)
        second_core, second_polarity = _normalize_polarity(second.text)

        if first_core and first_core == second_core:
            if first_polarity != second_polarity:
                findings.append(
                    Finding(
                        category="Conflicting business rules",
                        severity="High",
                        requirement=first,
                        related_requirement=second,
                        reason=(
                            "Contradiction: the statements have the same normalized "
                            "rule but express opposite explicit outcomes. Evidence: "
                            f"“{first.text}” versus “{second.text}”."
                        ),
                        recommendation=(
                            f"Resolve the contradiction between “{first.text}” and "
                            f"“{second.text}” with the business owner, then update "
                            "the source statement that is not authoritative."
                        ),
                    )
                )
            continue

        first_threshold = _threshold_condition(first.text)
        second_threshold = _threshold_condition(second.text)
        if first_threshold is None or second_threshold is None:
            continue

        different, disjoint = _threshold_conflict(first_threshold, second_threshold)
        if different:
            findings.append(
                Finding(
                    category="Conflicting business rules",
                    severity="High" if disjoint else "Medium",
                    requirement=first,
                    related_requirement=second,
                    reason=(
                        "Contradiction: the numeric conditions cannot both be true. "
                        f"Evidence: “{first.text}” versus “{second.text}”."
                        if disjoint
                        else "Inconsistent thresholds: the statements set different "
                        "numeric limits for the same inferred subject. "
                        f"Evidence: “{first.text}” versus “{second.text}”."
                    ),
                    recommendation=(
                        f"Confirm the subject, comparison operator, and threshold "
                        f"intended by “{first.text}” and “{second.text}”, then align "
                        "the uploaded statements with the decision."
                    ),
                )
            )
    return findings
