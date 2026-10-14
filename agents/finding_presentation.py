"""Human-readable presentation for requirement review findings."""

from agents.common import Finding


_TITLES = {
    "Ambiguous requirement": "The requirement needs a clearer definition",
    "Missing acceptance criteria": "Acceptance criteria need to be defined",
    "Conflicting business rules": "The document contains rules that need alignment",
    "Testability gap": "The expected result is difficult to verify",
    "Missing validation": "Validation behavior needs clarification",
    "Missing success criteria": "The successful outcome is not defined",
    "Missing prioritization": "The delivery priority is not recorded",
    "Modifiability concern": "This requirement may be easier to maintain if separated",
}

_EXPLANATIONS = {
    "Ambiguous requirement": (
        "The requirement uses wording that can be interpreted in more than one "
        "way. Without a shared threshold or observable definition, the team may "
        "implement or test different interpretations."
    ),
    "Missing acceptance criteria": (
        "The requirement does not provide explicit acceptance criteria showing "
        "how the team should verify the behavior. Development and testing may "
        "therefore reach different interpretations of when it is complete."
    ),
    "Conflicting business rules": (
        "The cited statements set different or incompatible expectations for "
        "the same rule. Unless the owner confirms how they relate, teams may "
        "implement or verify different outcomes."
    ),
    "Testability gap": (
        "The requirement does not give a clear observable result or measurable "
        "threshold to use as a pass/fail check. That can make it difficult for "
        "the team to agree whether the behavior is complete."
    ),
    "Missing validation": (
        "The requirement refers to validation, but it does not say what the "
        "system should do when an input or value fails validation. This leaves "
        "implementation and test expectations open to interpretation."
    ),
    "Missing success criteria": (
        "The requirement describes an action without stating the observable "
        "result that would confirm it succeeded. Developers and testers may "
        "therefore disagree about the expected outcome."
    ),
    "Missing prioritization": (
        "The requirement does not include an explicit delivery priority. "
        "Without an agreed priority, it may be difficult to plan scope and "
        "resolve competing delivery needs."
    ),
    "Modifiability concern": (
        "The requirement appears to combine multiple actions or outcomes. "
        "If those behaviors change independently, keeping them together may "
        "make review, traceability, and future updates harder."
    ),
}


def finding_category_display_title(category: str) -> str:
    """Return a concise, human-readable title while retaining custom categories."""
    if category.startswith("Missing business rule:"):
        detail = category.partition(":")[2].strip()
        label = "calculation" if detail == "calculation" else "workflow"
        return f"The {label} rule needs to be specified"
    if category.startswith("Missing NFR:"):
        area = category.partition(":")[2].strip()
        return f"The document does not identify {area.lower()} requirements"
    if category.startswith("Incomplete NFR:"):
        area = category.partition(":")[2].strip()
        return f"The {area.lower()} target needs more detail"
    return _TITLES.get(category, category)


def finding_display_title(finding: Finding) -> str:
    """Return a concise, human-readable title for one finding."""
    return finding_category_display_title(finding.category)


def finding_display_explanation(finding: Finding) -> str:
    """Explain the gap and its practical impact in plain QA language."""
    requirement_context = (
        f'For “{finding.requirement.text}”, '
        if finding.requirement is not None
        else ""
    )
    if finding.category.startswith("Missing NFR:"):
        area = finding.category.partition(":")[2].strip().lower()
        explanation = (
            f"The reviewed document does not identify requirements for {area}. "
            "This is not evidence that the system lacks this quality attribute; "
            "the document simply does not say whether it is in scope. "
            "If it applies, the team will need an agreed, verifiable target."
        )
    elif finding.category.startswith("Incomplete NFR:"):
        area = finding.category.partition(":")[2].strip().lower()
        explanation = (
            f"The document mentions {area}, but does not state a specific "
            "verification target or control. Without one, teams may interpret "
            "the expected level of support differently."
        )
    elif finding.category.startswith("Missing business rule:"):
        if finding.category.endswith(": calculation"):
            explanation = (
                "The requirement refers to a calculated value but does not "
                "state the calculation basis. Different assumptions about the "
                "inputs or rounding could produce different results."
            )
        else:
            explanation = (
                "The requirement names a workflow action but does not describe "
                "the relevant state, sequence, or decision condition. Those "
                "details can affect how the action is implemented and tested."
            )
    else:
        explanation = _EXPLANATIONS.get(finding.category, finding.reason)
        if finding.category == "Conflicting business rules":
            reason = finding.reason.casefold()
            if "inconsistent thresholds" in reason:
                explanation = (
                    "The statements set different numeric thresholds for what "
                    "appears to be the same subject. " + explanation
                )
            elif "cannot both be true" in reason or "opposite explicit outcomes" in reason:
                explanation = (
                    "The statements cannot both be true as written. " + explanation
                )
    if requirement_context:
        explanation = explanation[0].lower() + explanation[1:]
    return requirement_context + explanation
