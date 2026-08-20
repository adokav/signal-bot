from __future__ import annotations

import pytest

from memecoin_radar import (
    ExecutionMode,
    ExecutionPolicy,
    GateEvidence,
    GateState,
    HARD_GATES,
    MemecoinAssessment,
    Qualification,
)


def _gates(*, state: GateState = GateState.PASS, decision_at: int = 1_800_000_000):
    return {
        name: GateEvidence(
            gate=name,
            state=state,
            reason=f"{name} evidence",
            source="TEST",
            observed_at=decision_at - 10,
            available_at=decision_at - 5,
        )
        for name in HARD_GATES
    }


def _assessment(**overrides):
    values = dict(
        chain="SOLANA",
        token_address="So11111111111111111111111111111111111111112",
        symbol="TEST",
        decision_at=1_800_000_000,
        gates=_gates(),
        qualification=Qualification.WATCH,
        opportunity_score=55.0,
    )
    values.update(overrides)
    return MemecoinAssessment(**values)


def test_all_hard_gates_are_required():
    gates = _gates()
    gates.pop("EXECUTION")
    with pytest.raises(ValueError, match="every hard gate"):
        _assessment(gates=gates)


def test_failed_gate_cannot_be_averaged_away():
    gates = _gates()
    gates["LIQUIDITY"] = GateEvidence(
        gate="LIQUIDITY",
        state=GateState.FAIL,
        reason="exit liquidity insufficient",
        source="TEST",
        observed_at=1_799_999_990,
        available_at=1_799_999_995,
    )
    with pytest.raises(ValueError, match="must produce REJECT"):
        _assessment(gates=gates, qualification=Qualification.QUALIFIED)

    rejected = _assessment(
        gates=gates,
        qualification=Qualification.REJECT,
        opportunity_score=None,
    )
    assert rejected.qualification is Qualification.REJECT


def test_unknown_gate_never_becomes_watch_or_qualified():
    gates = _gates()
    gates["OWNERSHIP"] = GateEvidence(
        gate="OWNERSHIP",
        state=GateState.UNKNOWN,
        reason="holder data unavailable",
        source="TEST",
        observed_at=1_799_999_990,
        available_at=1_799_999_995,
    )
    with pytest.raises(ValueError, match="unknown hard gate"):
        _assessment(gates=gates, qualification=Qualification.WATCH)

    incomplete = _assessment(
        gates=gates,
        qualification=Qualification.DATA_INSUFFICIENT,
        opportunity_score=None,
    )
    assert incomplete.opportunity_score is None


def test_rejected_or_incomplete_token_has_no_opportunity_score():
    with pytest.raises(ValueError, match="must not receive opportunity score"):
        _assessment(qualification=Qualification.REJECT, opportunity_score=90.0)


def test_future_unavailable_evidence_is_rejected():
    gates = _gates()
    gates["IDENTITY"] = GateEvidence(
        gate="IDENTITY",
        state=GateState.PASS,
        reason="mint verified",
        source="TEST",
        observed_at=1_799_999_990,
        available_at=1_800_000_010,
    )
    with pytest.raises(ValueError, match="future-unavailable"):
        _assessment(gates=gates)


def test_v1_is_solana_only_and_research_cannot_authorize_trade():
    with pytest.raises(ValueError, match="Solana-only"):
        _assessment(chain="ETHEREUM")
    with pytest.raises(ValueError, match="cannot authorize"):
        _assessment(can_authorize_trade=True)


def test_execution_policy_is_forced_to_shadow_with_kill_switch():
    policy = ExecutionPolicy()
    assert policy.mode is ExecutionMode.SHADOW
    assert policy.kill_switch is True
    assert policy.signing_enabled is False

    with pytest.raises(ValueError, match="must remain SHADOW"):
        ExecutionPolicy(mode=ExecutionMode.CONFIRM)
    with pytest.raises(ValueError, match="signing"):
        ExecutionPolicy(signing_enabled=True)
    with pytest.raises(ValueError, match="kill switch"):
        ExecutionPolicy(kill_switch=False)
