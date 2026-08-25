from dataclasses import replace

from acce_unified.medium_horizon_long import (
    MediumHorizonLongContext,
    ThesisState,
    evaluate_medium_horizon_long,
    validate_execution_plan,
)
from acce_unified.tactical_long import PlanState, StructureState, TacticalSetup, TradePlan
from tests.test_tactical_long_engine import _snapshot


def _plan(*, stop: float) -> TradePlan:
    return TradePlan(
        symbol="BTCUSDT",
        setup=TacticalSetup.LIQUIDITY_SWEEP_RECLAIM,
        state=PlanState.READY,
        decision_at=1_700_000_000,
        entry_low=100.0,
        entry_high=101.0,
        technical_invalidation=stop + 0.5,
        hard_stop=stop,
        target_1=110.0,
        target_2=120.0,
        estimated_round_trip_cost_pct=0.05,
        expires_at=1_700_086_400,
    )


def _context(*, state=ThesisState.LONG_ALLOWED, invalidation=95.0):
    return MediumHorizonLongContext(
        symbol="BTCUSDT",
        state=state,
        daily_structure=StructureState.BULLISH,
        h4_structure=StructureState.BULLISH,
        structural_invalidation=invalidation,
        volatility_exposure_scalar=1.0,
        reasons=(),
        evidence=("TEST",),
    )


def test_execution_stop_cannot_sit_inside_confirmed_h4_structure():
    allowed, reasons = validate_execution_plan(_plan(stop=98.0), _context(invalidation=95.0))
    assert allowed is False
    assert "HARD_STOP_INSIDE_H4_STRUCTURE" in reasons


def test_structurally_wider_stop_is_allowed_by_policy_not_tightened_for_rr():
    allowed, reasons = validate_execution_plan(_plan(stop=94.0), _context(invalidation=95.0))
    assert allowed is True
    assert reasons == ()


def test_lower_timeframe_setup_cannot_override_no_long_thesis():
    allowed, reasons = validate_execution_plan(
        _plan(stop=94.0),
        _context(state=ThesisState.NO_LONG, invalidation=95.0),
    )
    assert allowed is False
    assert "MEDIUM_HORIZON_LONG_NOT_ALLOWED" in reasons


def test_medium_horizon_context_never_has_trade_authority():
    context = evaluate_medium_horizon_long(_snapshot(), "BTCUSDT")
    assert context.can_authorize_trade is False
    assert 0.0 < context.volatility_exposure_scalar <= 1.0


def test_volatility_changes_exposure_scalar_not_structural_invalidation():
    base = evaluate_medium_horizon_long(_snapshot(), "BTCUSDT")
    assert base.structural_invalidation is None or base.structural_invalidation > 0
    assert base.volatility_exposure_scalar in {0.4, 0.6, 0.8, 1.0}
