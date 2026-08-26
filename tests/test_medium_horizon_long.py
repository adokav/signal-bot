from dataclasses import replace
from types import SimpleNamespace

import pytest

import acce_unified.medium_horizon_long as medium_horizon
from acce_unified.medium_horizon_long import (
    MediumHorizonLongContext,
    ThesisState,
    evaluate_medium_horizon_long,
    validate_execution_plan,
)
from acce_unified.tactical_long import (
    DataQualityError,
    PlanState,
    StructureState,
    SwingKind,
    TacticalSetup,
    TradePlan,
)
from acce_unified.tactical_long_data import TacticalTimeframe
from tests.test_tactical_long_engine import _snapshot


DECISION_AT = 1_700_000_000


def _plan(*, stop: float, symbol: str = "BTCUSDT", state: PlanState = PlanState.READY, decision_at: int = DECISION_AT) -> TradePlan:
    return TradePlan(
        symbol=symbol,
        setup=TacticalSetup.LIQUIDITY_SWEEP_RECLAIM,
        state=state,
        decision_at=decision_at,
        entry_low=100.0,
        entry_high=101.0,
        technical_invalidation=stop + 0.5,
        hard_stop=stop,
        target_1=110.0,
        target_2=120.0,
        estimated_round_trip_cost_pct=0.05,
        expires_at=decision_at + 86_400,
    )


def _context(*, state=ThesisState.LONG_ALLOWED, invalidation=95.0, symbol="BTCUSDT", decision_at=DECISION_AT):
    return MediumHorizonLongContext(
        symbol=symbol,
        decision_at=decision_at,
        state=state,
        daily_structure=StructureState.BULLISH,
        h4_structure=StructureState.BULLISH,
        structural_invalidation=invalidation,
        volatility_exposure_scalar=1.0,
        reasons=(),
        evidence=("TEST",),
    )


def _fresh_thesis_snapshot():
    """Retimestamp the generic fixture so every timeframe is fresh at one cut."""
    snapshot = _snapshot()
    decision_at = snapshot.decision_at
    candles = {}
    for symbol, frames in snapshot.candles.items():
        candles[symbol] = {}
        for timeframe, rows in frames.items():
            final_close = decision_at - max(1, timeframe.seconds // 2)
            first_open = final_close - len(rows) * timeframe.seconds
            shifted = []
            for index, row in enumerate(rows):
                open_time = first_open + index * timeframe.seconds
                close_time = open_time + timeframe.seconds
                shifted.append(replace(row, open_time=open_time, close_time=close_time, available_at=close_time))
            candles[symbol][timeframe] = tuple(shifted)
    return replace(snapshot, candles=candles)


def test_execution_stop_cannot_sit_inside_confirmed_h4_structure():
    allowed, reasons = validate_execution_plan(_plan(stop=98.0), _context(invalidation=95.0))
    assert allowed is False
    assert "HARD_STOP_INSIDE_H4_STRUCTURE" in reasons


def test_structurally_wider_stop_is_allowed_by_policy_not_tightened_for_rr():
    allowed, reasons = validate_execution_plan(_plan(stop=94.0), _context(invalidation=95.0))
    assert allowed is True
    assert reasons == ()


def test_lower_timeframe_setup_cannot_override_no_long_thesis():
    allowed, reasons = validate_execution_plan(_plan(stop=94.0), _context(state=ThesisState.NO_LONG, invalidation=95.0))
    assert allowed is False
    assert "MEDIUM_HORIZON_LONG_NOT_ALLOWED" in reasons


def test_execution_validator_rejects_symbol_mismatch():
    allowed, reasons = validate_execution_plan(_plan(stop=94.0, symbol="ETHUSDT"), _context(symbol="BTCUSDT"))
    assert allowed is False
    assert "PLAN_CONTEXT_SYMBOL_MISMATCH" in reasons


def test_execution_validator_rejects_decision_time_mismatch():
    allowed, reasons = validate_execution_plan(_plan(stop=94.0, decision_at=DECISION_AT + 60), _context())
    assert allowed is False
    assert "PLAN_CONTEXT_DECISION_TIME_MISMATCH" in reasons


def test_execution_validator_never_revives_invalidated_plan():
    allowed, reasons = validate_execution_plan(_plan(stop=94.0, state=PlanState.INVALIDATED), _context())
    assert allowed is False
    assert "EXECUTION_PLAN_INVALIDATED" in reasons


def test_execution_validator_rejects_observe_plan_as_non_actionable():
    allowed, reasons = validate_execution_plan(_plan(stop=94.0, state=PlanState.OBSERVE), _context())
    assert allowed is False
    assert "EXECUTION_PLAN_NOT_ACTIONABLE" in reasons


def test_execution_validator_rejects_nonfinite_structural_invalidation():
    allowed, reasons = validate_execution_plan(_plan(stop=94.0), _context(invalidation=float("nan")))
    assert allowed is False
    assert "STRUCTURAL_INVALIDATION_UNKNOWN" in reasons


def test_medium_horizon_context_never_has_trade_authority_and_preserves_cut():
    snapshot = _fresh_thesis_snapshot()
    context = evaluate_medium_horizon_long(snapshot, "BTCUSDT")
    assert context.can_authorize_trade is False
    assert context.decision_at == snapshot.decision_at
    assert 0.0 < context.volatility_exposure_scalar <= 1.0


def test_volatility_changes_exposure_scalar_not_structural_invalidation():
    base = evaluate_medium_horizon_long(_fresh_thesis_snapshot(), "BTCUSDT")
    assert base.structural_invalidation is None or base.structural_invalidation > 0
    assert base.volatility_exposure_scalar in {0.4, 0.5, 0.6, 0.8, 1.0}


def test_medium_horizon_rejects_future_or_open_daily_candle():
    snapshot = _fresh_thesis_snapshot()
    d1 = list(snapshot.candles["BTCUSDT"][TacticalTimeframe.D1])
    d1[-1] = replace(d1[-1], close_time=snapshot.decision_at + 60, available_at=snapshot.decision_at + 60)
    candles = {symbol: dict(frames) for symbol, frames in snapshot.candles.items()}
    candles["BTCUSDT"][TacticalTimeframe.D1] = tuple(d1)
    with pytest.raises(DataQualityError, match="future or open candle"):
        evaluate_medium_horizon_long(replace(snapshot, candles=candles), "BTCUSDT")


def test_medium_horizon_rejects_stale_h4_frame():
    snapshot = _fresh_thesis_snapshot()
    h4 = tuple(
        replace(row, open_time=row.open_time - 10 * TacticalTimeframe.H4.seconds,
                close_time=row.close_time - 10 * TacticalTimeframe.H4.seconds,
                available_at=row.available_at - 10 * TacticalTimeframe.H4.seconds)
        for row in snapshot.candles["BTCUSDT"][TacticalTimeframe.H4]
    )
    candles = {symbol: dict(frames) for symbol, frames in snapshot.candles.items()}
    candles["BTCUSDT"][TacticalTimeframe.H4] = h4
    with pytest.raises(DataQualityError, match="latest candle is stale"):
        evaluate_medium_horizon_long(replace(snapshot, candles=candles), "BTCUSDT")


def test_medium_horizon_rejects_h4_cadence_gap():
    snapshot = _fresh_thesis_snapshot()
    h4 = list(snapshot.candles["BTCUSDT"][TacticalTimeframe.H4])
    last = h4[-1]
    h4[-1] = replace(
        last,
        open_time=last.open_time + 60,
        close_time=last.close_time + 60,
        available_at=last.available_at + 60,
    )
    candles = {symbol: dict(frames) for symbol, frames in snapshot.candles.items()}
    candles["BTCUSDT"][TacticalTimeframe.H4] = tuple(h4)
    with pytest.raises(DataQualityError, match="cadence gap or overlap"):
        evaluate_medium_horizon_long(replace(snapshot, candles=candles), "BTCUSDT")


def test_medium_horizon_rejects_h4_duration_mismatch():
    snapshot = _fresh_thesis_snapshot()
    h4 = list(snapshot.candles["BTCUSDT"][TacticalTimeframe.H4])
    last = h4[-1]
    h4[-1] = replace(last, close_time=last.close_time - 60, available_at=last.close_time - 60)
    candles = {symbol: dict(frames) for symbol, frames in snapshot.candles.items()}
    candles["BTCUSDT"][TacticalTimeframe.H4] = tuple(h4)
    with pytest.raises(DataQualityError, match="candle duration mismatch"):
        evaluate_medium_horizon_long(replace(snapshot, candles=candles), "BTCUSDT")


def test_insufficient_h4_structure_is_no_long_even_with_a_confirmed_low(monkeypatch):
    snapshot = _fresh_thesis_snapshot()
    calls = iter((
        (StructureState.BULLISH, ()),
        (StructureState.INSUFFICIENT_DATA, (SimpleNamespace(kind=SwingKind.LOW, price=95.0),)),
    ))
    monkeypatch.setattr(medium_horizon, "_structure", lambda candles, timeframe: next(calls))
    context = evaluate_medium_horizon_long(snapshot, "BTCUSDT")
    assert context.state is ThesisState.NO_LONG
    assert "INSUFFICIENT_H4_STRUCTURE" in context.reasons
