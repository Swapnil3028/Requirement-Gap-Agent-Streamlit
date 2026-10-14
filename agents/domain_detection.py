"""Detect a likely business domain and provide optional domain QA prompts."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from agents.common import Requirement


@dataclass(frozen=True)
class BusinessDomainAssessment:
    domain: str
    confidence: int
    evidence: tuple[str, ...]
    alternative_domains: tuple[str, ...]
    review_questions: tuple[str, ...]


_DOMAIN_PROFILES: dict[str, tuple[tuple[tuple[str, int], ...], tuple[str, ...]]] = {
    "Retail": (
        (
            ("retail", 3),
            ("point of sale", 3),
            ("pos", 2),
            ("in-store", 2),
            ("store", 1),
            ("inventory", 2),
            ("merchandise", 2),
            ("stock level", 2),
            ("product catalog", 1),
        ),
        (
            "Which sales channels and store locations are in scope?",
            "How should pricing, discounts, and applicable taxes be determined?",
            "What should happen when an item is unavailable or stock changes?",
            "Which payment methods and return conditions apply?",
        ),
    ),
    "E-commerce": (
        (
            ("e-commerce", 4),
            ("ecommerce", 4),
            ("online store", 4),
            ("shopping cart", 3),
            ("checkout", 2),
            ("online payment", 4),
            ("pay online", 4),
            ("place an order", 2),
            ("order placement", 2),
            ("product catalog", 1),
            ("payment gateway", 3),
        ),
        (
            "Which payment methods and payment-provider behaviors are in scope?",
            "What should happen when payment is declined, delayed, or retried?",
            "When is an order considered placed and confirmed?",
            "Which cancellation, return, and refund conditions apply?",
        ),
    ),
    "Banking": (
        (
            ("banking", 4),
            ("bank account", 4),
            ("bank", 3),
            ("wire transfer", 4),
            ("fund transfer", 3),
            ("transaction limit", 4),
            ("withdrawal", 2),
            ("deposit", 2),
            ("loan", 2),
            ("fraud detection", 3),
            ("audit trail", 2),
            ("account holder", 3),
        ),
        (
            "Which fraud checks or transaction-monitoring controls apply?",
            "What transaction limits apply by account, customer, or channel?",
            "Which events must be recorded in the audit trail, and for how long?",
            "What authorization or approval is required before a transaction completes?",
        ),
    ),
    "Insurance": (
        (
            ("insurance", 4),
            ("policyholder", 3),
            ("insurance policy", 4),
            ("coverage", 2),
            ("premium", 2),
            ("claim", 2),
            ("underwriting", 3),
            ("beneficiary", 2),
            ("deductible", 2),
        ),
        (
            "Which policy types, eligibility conditions, and coverage limits apply?",
            "Which exclusions or deductible rules affect coverage?",
            "How should premiums or claim amounts be calculated?",
            "What review, approval, and notification steps apply to a claim?",
        ),
    ),
    "Healthcare": (
        (
            ("healthcare", 4),
            ("health care", 4),
            ("patient", 2),
            ("clinical", 3),
            ("diagnosis", 3),
            ("medical record", 3),
            ("prescription", 3),
            ("care provider", 2),
            ("hipaa", 4),
        ),
        (
            "What patient identity and consent checks are required?",
            "Which privacy, access, and disclosure rules apply to patient information?",
            "What clinical review or escalation is required before an action is finalized?",
            "What safety-critical errors or exceptions must be handled explicitly?",
        ),
    ),
    "Travel": (
        (
            ("travel", 3),
            ("flight", 3),
            ("airline", 3),
            ("hotel", 3),
            ("itinerary", 3),
            ("reservation", 2),
            ("booking", 2),
            ("check-in", 2),
            ("travelers", 2),
        ),
        (
            "How should availability and price changes be handled during booking?",
            "What confirms a booking, and what happens if confirmation is delayed?",
            "Which change, cancellation, and refund conditions apply?",
            "Which traveler, ticket, or reservation details must be validated?",
        ),
    ),
    "Manufacturing": (
        (
            ("manufacturing", 4),
            ("production line", 3),
            ("assembly", 3),
            ("factory", 3),
            ("machine", 2),
            ("work order", 3),
            ("quality inspection", 3),
            ("tolerance", 2),
            ("batch production", 3),
            ("bill of materials", 3),
        ),
        (
            "Which production tolerances and acceptable quality ranges apply?",
            "What inspection or approval is required before a batch is accepted?",
            "How should machine downtime or a failed production step be handled?",
            "Which materials, component versions, and batch details need traceability?",
        ),
    ),
}


def detect_business_domain(
    requirements: Sequence[Requirement],
) -> BusinessDomainAssessment:
    """Infer the strongest domain from the full document batch, not one statement."""
    corpus = "\n".join(
        " ".join(
            part
            for part in (requirement.text, requirement.acceptance_criteria or "")
            if part
        )
        for requirement in requirements
    ).casefold()

    scores: dict[str, int] = {}
    evidence_by_domain: dict[str, tuple[str, ...]] = {}
    for domain, (cues, _) in _DOMAIN_PROFILES.items():
        evidence = tuple(
            cue
            for cue, _ in cues
            if re.search(r"(?<!\w)" + re.escape(cue) + r"(?!\w)", corpus)
        )
        evidence_by_domain[domain] = evidence
        scores[domain] = sum(
            weight
            for cue, weight in cues
            if re.search(r"(?<!\w)" + re.escape(cue) + r"(?!\w)", corpus)
        )

    ordered = sorted(scores, key=lambda domain: scores[domain], reverse=True)
    top_domain = ordered[0]
    top_score = scores[top_domain]
    second_score = scores[ordered[1]] if len(ordered) > 1 else 0
    if top_score < 2 or top_score == second_score:
        alternatives = tuple(
            domain for domain in ordered if scores[domain] == top_score and top_score > 0
        )
        return BusinessDomainAssessment(
            domain="Undetermined",
            confidence=0,
            evidence=tuple(
                dict.fromkeys(
                    cue
                    for domain in alternatives
                    for cue in evidence_by_domain[domain]
                )
            ),
            alternative_domains=alternatives,
            review_questions=(
                "Which business domain should guide the stakeholder review?",
            ),
        )

    margin = top_score - second_score
    confidence = min(95, 50 + top_score * 5 + margin * 3)
    return BusinessDomainAssessment(
        domain=top_domain,
        confidence=confidence,
        evidence=evidence_by_domain[top_domain],
        alternative_domains=(),
        review_questions=_DOMAIN_PROFILES[top_domain][1],
    )
