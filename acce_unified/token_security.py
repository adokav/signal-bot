"""Fail-closed token security and early-opportunity research contracts.

The module is intentionally chain-neutral. Chain-specific providers translate
raw observations into these evidence records. It has no wallet, signing,
execution, portfolio-sizing or order authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class RiskDomain(str, Enum):
    CONTRACT_CONTROL = "CONTRACT_CONTROL"
    LIQUIDITY_EXIT = "LIQUIDITY_EXIT"
    OWNERSHIP_INSIDER = "OWNERSHIP_INSIDER"
    TOKENOMICS = "TOKENOMICS"


class EvidenceState(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    UNKNOWN = "UNKNOWN"
    INVALID = "INVALID"


class Severity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SecurityDecision(str, Enum):
    REJECT = "REJECT"
    INTELLIGENCE_INCOMPLETE = "INTELLIGENCE_INCOMPLETE"
    OBSERVE = "OBSERVE"
    ELIGIBLE_FOR_OPPORTUNITY_RESEARCH = "ELIGIBLE_FOR_OPPORTUNITY_RESEARCH"


HARD_VETO_CODES = frozenset({
    "CONFIRMED_HONEYPOT",
    "FAKE_CONTRACT_IDENTITY",
    "HOSTILE_SELL_CONTROL",
    "UNBOUNDED_MINT_UNTRUSTED",
    "REMOVABLE_CORE_LIQUIDITY",
})


@dataclass(frozen=True)
class SecurityEvidence:
    code: str
    domain: RiskDomain
    state: EvidenceState
    severity: Severity
    source: str
    observed_at: int
    available_at: int
    confidence: float
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.source.strip():
            raise ValueError("evidence code and source are required")
        if self.observed_at <= 0 or self.available_at <= 0:
            raise ValueError("evidence timestamps must be positive")
        if self.available_at < self.observed_at:
            raise ValueError("available_at cannot precede observed_at")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be inside [0, 1]")

    @property
    def is_hard_veto(self) -> bool:
        return (
            self.code in HARD_VETO_CODES
            and self.state is EvidenceState.NEGATIVE
            and self.severity is Severity.CRITICAL
        )


@dataclass(frozen=True)
class DomainAssessment:
    domain: RiskDomain
    risk_score: int
    evidence_count: int
    unknown_count: int
    critical_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.risk_score <= 100:
            raise ValueError("risk_score must be inside [0, 100]")
        if self.evidence_count < 0 or self.unknown_count < 0:
            raise ValueError("evidence counters cannot be negative")


@dataclass(frozen=True)
class TokenSecurityAssessment:
    chain: str
    contract_address: str
    decision_at: int
    decision: SecurityDecision
    intelligence_quality: int
    domains: tuple[DomainAssessment, ...]
    hard_vetoes: tuple[str, ...]
    reasons: tuple[str, ...]
    can_authorize_trade: bool = False

    def __post_init__(self) -> None:
        if not self.chain.strip() or not self.contract_address.strip():
            raise ValueError("chain and contract_address are required")
        if self.decision_at <= 0:
            raise ValueError("decision_at must be positive")
        if not 0 <= self.intelligence_quality <= 100:
            raise ValueError("intelligence_quality must be inside [0, 100]")
        if self.can_authorize_trade:
            raise ValueError("security research cannot authorize trades")


_SEVERITY_POINTS = {
    Severity.INFO: 0,
    Severity.LOW: 12,
    Severity.MEDIUM: 30,
    Severity.HIGH: 60,
    Severity.CRITICAL: 100,
}


def _assess_domain(domain: RiskDomain, rows: list[SecurityEvidence]) -> DomainAssessment:
    relevant = [row for row in rows if row.domain is domain]
    unknown = sum(row.state in {EvidenceState.UNKNOWN, EvidenceState.INVALID} for row in relevant)
    negative_points = [
        round(_SEVERITY_POINTS[row.severity] * row.confidence)
        for row in relevant
        if row.state is EvidenceState.NEGATIVE
    ]
    risk_score = max(negative_points, default=0)
    critical = tuple(sorted({row.code for row in relevant if row.is_hard_veto}))
    return DomainAssessment(
        domain=domain,
        risk_score=min(100, int(risk_score)),
        evidence_count=len(relevant),
        unknown_count=unknown,
        critical_codes=critical,
    )


def assess_token_security(
    *,
    chain: str,
    contract_address: str,
    decision_at: int,
    evidence: Iterable[SecurityEvidence],
    minimum_intelligence_quality: int = 70,
    observe_risk_threshold: int = 35,
) -> TokenSecurityAssessment:
    """Create a deterministic assessment without blending away critical risks."""
    rows = list(evidence)
    future = [row.code for row in rows if row.available_at > decision_at]
    if future:
        raise ValueError(f"future evidence unavailable at decision time: {future}")

    domains = tuple(_assess_domain(domain, rows) for domain in RiskDomain)
    hard_vetoes = tuple(sorted({code for item in domains for code in item.critical_codes}))
    known = sum(row.state in {EvidenceState.POSITIVE, EvidenceState.NEGATIVE} for row in rows)
    valid_total = sum(row.state is not EvidenceState.INVALID for row in rows)
    intelligence_quality = round(100 * known / valid_total) if valid_total else 0
    max_risk = max((item.risk_score for item in domains), default=0)

    reasons: list[str] = []
    if hard_vetoes:
        decision = SecurityDecision.REJECT
        reasons.append("critical hard veto present")
    elif intelligence_quality < minimum_intelligence_quality:
        decision = SecurityDecision.INTELLIGENCE_INCOMPLETE
        reasons.append("insufficient point-in-time intelligence")
    elif max_risk >= observe_risk_threshold:
        decision = SecurityDecision.OBSERVE
        reasons.append("non-critical risk requires observation")
    else:
        decision = SecurityDecision.ELIGIBLE_FOR_OPPORTUNITY_RESEARCH
        reasons.append("security gates passed for research only")

    return TokenSecurityAssessment(
        chain=chain.upper(),
        contract_address=contract_address,
        decision_at=decision_at,
        decision=decision,
        intelligence_quality=intelligence_quality,
        domains=domains,
        hard_vetoes=hard_vetoes,
        reasons=tuple(reasons),
        can_authorize_trade=False,
    )
