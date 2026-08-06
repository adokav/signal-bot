from __future__ import annotations

import pytest

from acce_unified.token_security import (
    EvidenceState,
    RiskDomain,
    SecurityDecision,
    SecurityEvidence,
    Severity,
    assess_token_security,
)

NOW = 1_800_000_000


def evidence(
    code: str,
    domain: RiskDomain,
    state: EvidenceState,
    severity: Severity,
    *,
    confidence: float = 1.0,
    available_at: int = NOW,
) -> SecurityEvidence:
    return SecurityEvidence(
        code=code,
        domain=domain,
        state=state,
        severity=severity,
        source="TEST_PROVIDER",
        observed_at=NOW - 60,
        available_at=available_at,
        confidence=confidence,
    )


def test_critical_honeypot_cannot_be_averaged_away():
    rows = [
        evidence("CONFIRMED_HONEYPOT", RiskDomain.CONTRACT_CONTROL, EvidenceState.NEGATIVE, Severity.CRITICAL),
        evidence("LP_LOCK_VERIFIED", RiskDomain.LIQUIDITY_EXIT, EvidenceState.POSITIVE, Severity.INFO),
        evidence("DISTRIBUTION_HEALTHY", RiskDomain.OWNERSHIP_INSIDER, EvidenceState.POSITIVE, Severity.INFO),
        evidence("DILUTION_LOW", RiskDomain.TOKENOMICS, EvidenceState.POSITIVE, Severity.INFO),
    ]
    result = assess_token_security(
        chain="solana",
        contract_address="TokenMint",
        decision_at=NOW,
        evidence=rows,
    )
    assert result.decision is SecurityDecision.REJECT
    assert result.hard_vetoes == ("CONFIRMED_HONEYPOT",)
    assert result.can_authorize_trade is False


def test_unknown_evidence_never_becomes_safe():
    rows = [
        evidence("MINT_AUTHORITY", RiskDomain.CONTRACT_CONTROL, EvidenceState.UNKNOWN, Severity.HIGH),
        evidence("LP_LOCK", RiskDomain.LIQUIDITY_EXIT, EvidenceState.UNKNOWN, Severity.HIGH),
        evidence("HOLDER_CLUSTERING", RiskDomain.OWNERSHIP_INSIDER, EvidenceState.UNKNOWN, Severity.HIGH),
        evidence("UNLOCK_SCHEDULE", RiskDomain.TOKENOMICS, EvidenceState.UNKNOWN, Severity.HIGH),
    ]
    result = assess_token_security(
        chain="ethereum",
        contract_address="0xabc",
        decision_at=NOW,
        evidence=rows,
    )
    assert result.intelligence_quality == 0
    assert result.decision is SecurityDecision.INTELLIGENCE_INCOMPLETE


def test_noncritical_high_risk_stays_observe():
    rows = [
        evidence("MUTABLE_TAX", RiskDomain.CONTRACT_CONTROL, EvidenceState.NEGATIVE, Severity.HIGH, confidence=0.8),
        evidence("EXIT_LIQUIDITY_OK", RiskDomain.LIQUIDITY_EXIT, EvidenceState.POSITIVE, Severity.INFO),
        evidence("HOLDER_DISTRIBUTION_OK", RiskDomain.OWNERSHIP_INSIDER, EvidenceState.POSITIVE, Severity.INFO),
        evidence("SUPPLY_KNOWN", RiskDomain.TOKENOMICS, EvidenceState.POSITIVE, Severity.INFO),
    ]
    result = assess_token_security(
        chain="base",
        contract_address="0xdef",
        decision_at=NOW,
        evidence=rows,
    )
    assert result.decision is SecurityDecision.OBSERVE
    assert not result.hard_vetoes


def test_complete_low_risk_case_is_research_eligible_not_trade_authorized():
    rows = [
        evidence("MINT_REVOKED", RiskDomain.CONTRACT_CONTROL, EvidenceState.POSITIVE, Severity.INFO),
        evidence("SELL_SIMULATION_OK", RiskDomain.CONTRACT_CONTROL, EvidenceState.POSITIVE, Severity.INFO),
        evidence("EXIT_LIQUIDITY_OK", RiskDomain.LIQUIDITY_EXIT, EvidenceState.POSITIVE, Severity.INFO),
        evidence("HOLDER_DISTRIBUTION_OK", RiskDomain.OWNERSHIP_INSIDER, EvidenceState.POSITIVE, Severity.INFO),
        evidence("SUPPLY_KNOWN", RiskDomain.TOKENOMICS, EvidenceState.POSITIVE, Severity.INFO),
    ]
    result = assess_token_security(
        chain="solana",
        contract_address="Mint123",
        decision_at=NOW,
        evidence=rows,
    )
    assert result.decision is SecurityDecision.ELIGIBLE_FOR_OPPORTUNITY_RESEARCH
    assert result.intelligence_quality == 100
    assert result.can_authorize_trade is False


def test_future_evidence_is_rejected():
    rows = [
        evidence(
            "SELL_SIMULATION_OK",
            RiskDomain.CONTRACT_CONTROL,
            EvidenceState.POSITIVE,
            Severity.INFO,
            available_at=NOW + 1,
        )
    ]
    with pytest.raises(ValueError, match="future evidence"):
        assess_token_security(
            chain="solana",
            contract_address="Mint123",
            decision_at=NOW,
            evidence=rows,
        )


def test_available_time_cannot_precede_observation_time():
    with pytest.raises(ValueError, match="available_at"):
        SecurityEvidence(
            code="INVALID_TIME",
            domain=RiskDomain.CONTRACT_CONTROL,
            state=EvidenceState.UNKNOWN,
            severity=Severity.INFO,
            source="TEST",
            observed_at=NOW,
            available_at=NOW - 1,
            confidence=0.5,
        )
