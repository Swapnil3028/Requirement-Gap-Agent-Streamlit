"""Detect evidence-based business-rule and NFR coverage gaps."""

import re
from collections.abc import Sequence

from agents.common import Finding, Requirement


_NFR_TERMS = {
    "Performance": re.compile(
        r"\b(?:performance|response time|latency|throughput|fast|quickly|"
        r"load time|within \d+)\b",
        re.IGNORECASE,
    ),
    "Security": re.compile(
        r"\b(?:security|secure|encryption|encrypted|cybersecurity|"
        r"vulnerability|threat protection)\b",
        re.IGNORECASE,
    ),
    "Authorization": re.compile(
        r"\b(?:authorization|authorisation|authorized|authorised|"
        r"access control|permissions?|roles?|privileges?)\b",
        re.IGNORECASE,
    ),
    "Authentication": re.compile(
        r"\b(?:authentication|authenticate|authenticated|sign[\s-]?in|"
        r"log[\s-]?in|identity verification|multi[\s-]?factor)\b",
        re.IGNORECASE,
    ),
    "Availability": re.compile(
        r"\b(?:availability|available|uptime|downtime|recovery time|"
        r"disaster recovery)\b",
        re.IGNORECASE,
    ),
    "Scalability": re.compile(
        r"\b(?:scalability|scalable|scale|concurrent users|user capacity|"
        r"volume growth)\b",
        re.IGNORECASE,
    ),
    "Audit": re.compile(
        r"\b(?:audit|audit trail|audit log|traceability)\b",
        re.IGNORECASE,
    ),
    "Logging": re.compile(r"\b(?:logging|logs|log entries|logged)\b", re.IGNORECASE),
    "Compliance": re.compile(
        r"\b(?:compliance|compliant|complies|comply|regulation|regulatory|standard|"
        r"data protection|privacy law)\b",
        re.IGNORECASE,
    ),
    "Accessibility": re.compile(
        r"\b(?:accessibility|accessible|wcag|screen reader|keyboard navigation|"
        r"assistive technology)\b",
        re.IGNORECASE,
    ),
    "Localization": re.compile(
        r"\b(?:localization|localisation|localized|localised|internationalization|"
        r"internationalisation|locale|time zone|currency format|date format|"
        r"language support|supported languages?|translation)\b",
        re.IGNORECASE,
    ),
    "Recovery": re.compile(
        r"\b(?:recovery|recoverability|restore|restoration|backup|back-up|"
        r"disaster recovery|recovery point|recovery time|rpo|rto)\b",
        re.IGNORECASE,
    ),
}
_NFR_DETAIL = {
    "Performance": re.compile(
        r"\b\d+(?:\.\d+)?\s*(?:milliseconds?|ms|seconds?|requests?/second|"
        r"transactions?/second|concurrent users?)\b",
        re.IGNORECASE,
    ),
    "Security": re.compile(
        r"\b(?:encrypt\w*|TLS|HTTPS|vulnerabilit\w*|penetration test|"
        r"threat model|security patch)\b",
        re.IGNORECASE,
    ),
    "Authorization": re.compile(
        r"\b(?:role[- ]based|permission matrix|least privilege|"
        r"only (?:the )?(?:owner|role|administrator)|access denied)\b",
        re.IGNORECASE,
    ),
    "Authentication": re.compile(
        r"\b(?:multi[- ]factor|MFA|single sign[- ]on|SSO|"
        r"password policy|identity provider|authenticator)\b",
        re.IGNORECASE,
    ),
    "Audit": re.compile(
        r"\b(?:audit (?:event|record|trail|log)|who did what|"
        r"actor and timestamp|audit record retention)\b",
        re.IGNORECASE,
    ),
    "Logging": re.compile(
        r"\b(?:log level|log retention|logs? (?:are )?retained|timestamped log|"
        r"correlation id|structured log|logs? retained)\b",
        re.IGNORECASE,
    ),
    "Availability": re.compile(
        r"\b\d+(?:\.\d+)?\s*%\s*(?:availability|uptime)\b"
        r"|\b(?:uptime|availability)\s*(?:of|>=|at least)\s*\d+(?:\.\d+)?\s*%",
        re.IGNORECASE,
    ),
    "Scalability": re.compile(
        r"\b\d+(?:,\d{3})*\s*(?:concurrent users?|requests?/second|"
        r"transactions?/second|records?)\b",
        re.IGNORECASE,
    ),
    "Accessibility": re.compile(
        r"\b(?:WCAG\s*[12]\.[0-9]+\s*(?:A{1,3})?|"
        r"keyboard(?:-only)? navigation|screen reader|"
        r"accessibility acceptance test)\b",
        re.IGNORECASE,
    ),
    "Compliance": re.compile(
        r"\b(?:GDPR|HIPAA|PCI[\s-]?DSS|SOC\s*2|ISO\s*27001|"
        r"CCPA|regulation [A-Z0-9-]+)\b",
        re.IGNORECASE,
    ),
    "Localization": re.compile(
        r"\b(?:locale [a-z]{2}(?:-[A-Z]{2})?|"
        r"supported languages?|date format|currency format|"
        r"time zone|translated into)\b",
        re.IGNORECASE,
    ),
    "Recovery": re.compile(
        r"\b(?:RTO|RPO|recovery time objective|recovery point objective|"
        r"backup every|restore within|failover within)\b",
        re.IGNORECASE,
    ),
}
_CALCULATION_CUE = re.compile(
    r"\b(?:calculate|calculates|calculated|calculation|compute|computes|"
    r"computed|total|subtotal|fee|fees|tax|taxes|discount|discounts|"
    r"interest|commission|score|rating)\b",
    re.IGNORECASE,
)
_CALCULATION_RULE = re.compile(
    r"\b(?:formula|calculation rule|calculated as|computed as|"
    r"percentage of|rate of|sum of|based on|round(?:ed|ing)? to|"
    r"multiply|divid(?:e|ed) by|subtract(?:ed)?|add(?:ed)? to)\b"
    r"|\b\d+(?:\.\d+)?\s*%",
    re.IGNORECASE,
)
_WORKFLOW_ACTION = re.compile(
    r"\b(?:approve|approves|approved|cancel|cancels|cancelled|canceled|"
    r"complete|completes|completed|process|processes|processed|"
    r"submit|submits|submitted|transfer|transfers|transferred|"
    r"refund|refunds|refunded|ship|ships|shipped|activate|activates|"
    r"deactivate|deactivates|renew|renews|suspend|suspends)\b",
    re.IGNORECASE,
)
_WORKFLOW_DETAIL = re.compile(
    r"\b(?:before|after|until|when|once|unless|provided that|"
    r"status|state|transition|step \d+|first|then)\b",
    re.IGNORECASE,
)
_VALIDATION_CUE = re.compile(
    r"\b(?:valid\w*|invalid|input|field|format|length|"
    r"required field|mandatory field|eligib\w*|constraint|"
    r"boundary|range|allowed value)\b",
    re.IGNORECASE,
)
_VALIDATION_OUTCOME = re.compile(
    r"\b(?:reject\w*|error|message|warn\w*|block\w*|prevent\w*|"
    r"accept\w*|display\w*|return\w*|save\w*|allow\w*)\b",
    re.IGNORECASE,
)
_SUCCESS_CRITERION = re.compile(
    r"\b(?:accept\w*|block\w*|confirm\w*|creat\w*|delet\w*|den(?:y|ies|ied)|"
    r"display\w*|log\w*|reject\w*|redirect\w*|return\w*|respond\w*|"
    r"save\w*|send\w*|show\w*|store\w*|update\w*|complete\w*)\b"
    r"|\b\d+(?:\.\d+)?\s*(?:%|milliseconds?|ms|seconds?|minutes?|hours?|"
    r"days?|weeks?|months?|years?|items?|records?|users?|requests?)\b",
    re.IGNORECASE,
)


def _document_scope(requirements: Sequence[Requirement]) -> str:
    return ", ".join(dict.fromkeys(requirement.source for requirement in requirements))


def detect_missing_nfrs(requirements: Sequence[Requirement]) -> list[Finding]:
    """Check each NFR area for mention and actionable, area-specific detail."""
    if not requirements:
        return []

    corpus = "\n".join(requirement.text for requirement in requirements)
    scope = _document_scope(requirements)
    findings = []
    for area, pattern in _NFR_TERMS.items():
        mentions_area = bool(pattern.search(corpus))
        has_detail = bool(_NFR_DETAIL[area].search(corpus))
        if not mentions_area:
            category = f"Missing NFR: {area}"
            issue = f"No {area.lower()} requirement was identified"
        elif has_detail:
            continue
        else:
            category = f"Incomplete NFR: {area}"
            issue = f"{area} is mentioned, but no area-specific verification target or control was identified"
        findings.append(
            Finding(
                category=category,
                severity="Medium",
                requirement=None,
                document_scope=scope,
                reason=(
                    f"{issue} in the uploaded document batch ({scope}). "
                    "This is a document-level coverage prompt, not evidence that "
                    "the system lacks this quality attribute."
                ),
                recommendation=(
                    f"Confirm whether {area.lower()} requirements apply. If they "
                    "do, add a source-owned, testable target or control specific "
                    "to this quality attribute."
                ),
                clarification_question=(
                    f"Do these uploaded requirements need {area.lower()} criteria? "
                    "If yes, what measurable target and verification conditions "
                    "does the Product Owner approve?"
                ),
            )
        )
    return findings


def detect_missing_validation(requirements: Sequence[Requirement]) -> list[Finding]:
    """Flag stated input/eligibility validation without an observable outcome."""
    findings = []
    for requirement in requirements:
        if (
            _VALIDATION_CUE.search(requirement.text)
            and not _VALIDATION_OUTCOME.search(requirement.text)
        ):
            findings.append(
                Finding(
                    category="Missing validation",
                    severity="Medium",
                    requirement=requirement,
                    reason=(
                        f"The uploaded requirement “{requirement.text}” mentions "
                        "validation or constrained input but does not specify the "
                        "observable result for invalid or out-of-range values."
                    ),
                    recommendation=(
                        "Specify the validation condition and the required system "
                        "response for valid and invalid input."
                    ),
                    clarification_question=(
                        f"For “{requirement.text}”, which values are valid, and "
                        "what should the system display or do when validation fails?"
                    ),
                )
            )
    return findings


def detect_missing_success_criteria(
    requirements: Sequence[Requirement],
) -> list[Finding]:
    """Flag behavior statements without a source-stated observable success result."""
    findings = []
    for requirement in requirements:
        if (
            _WORKFLOW_ACTION.search(requirement.text)
            and not _SUCCESS_CRITERION.search(requirement.text)
        ):
            findings.append(
                Finding(
                    category="Missing success criteria",
                    severity="High",
                    requirement=requirement,
                    reason=(
                        f"The uploaded requirement “{requirement.text}” states an "
                        "action but does not identify an observable success "
                        "condition or outcome."
                    ),
                    recommendation=(
                        "Add the expected observable result that determines "
                        "whether this behavior succeeds."
                    ),
                    clarification_question=(
                        f"For “{requirement.text}”, what observable result confirms "
                        "that the stated action succeeded?"
                    ),
                )
            )
    return findings


def detect_undefined_business_rules(
    requirements: Sequence[Requirement],
) -> list[Finding]:
    """Flag explicit calculations or workflow actions whose governing rules are absent."""
    findings = []
    for requirement in requirements:
        if _CALCULATION_CUE.search(requirement.text) and not _CALCULATION_RULE.search(
            requirement.text
        ):
            findings.append(
                Finding(
                    category="Missing business rule: calculation",
                    severity="Medium",
                    requirement=requirement,
                    reason=(
                        f"The uploaded statement “{requirement.text}” refers to a "
                        "calculation or derived value but does not state its formula "
                        "or calculation basis."
                    ),
                    recommendation=(
                        f"Specify the calculation rule for the value named in "
                        f"“{requirement.text}”, including inputs, rounding, and "
                        "applicable conditions if they are relevant."
                    ),
                    clarification_question=(
                        f"How is the value in “{requirement.text}” calculated, "
                        "which inputs and conditions apply, and how should rounding "
                        "be handled?"
                    ),
                )
            )
            continue

        if _WORKFLOW_ACTION.search(requirement.text) and not _WORKFLOW_DETAIL.search(
            requirement.text
        ):
            findings.append(
                Finding(
                    category="Missing business rule: workflow",
                    severity="Medium",
                    requirement=requirement,
                    reason=(
                        f"The uploaded statement “{requirement.text}” names a "
                        "workflow action but does not state its sequence, applicable "
                        "state, or decision condition."
                    ),
                    recommendation=(
                        f"Confirm whether “{requirement.text}” needs explicit "
                        "workflow steps, allowed states, and decision conditions; "
                        "add only the rules approved by the Product Owner."
                    ),
                    clarification_question=(
                        f"For “{requirement.text}”, what precondition or state "
                        "allows the action, what steps occur, and what outcome "
                        "should be observable?"
                    ),
                )
            )
    return findings
