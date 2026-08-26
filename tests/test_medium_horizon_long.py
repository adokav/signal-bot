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


def _validate(plan, context, *, evaluated_at=DECISION_AT):
    return validate_execution_plan(plan, context, evaluated_at=evaluated_at)


def _fresh_thesis_snapshot(*, inclusive_close: bool = True):
    """Retimestamp the generic fixture into fresh, UTC-aligned buckets.

    MEXC uses inclusive close timestamps (open+seconds-1). Replay fixtures may
    use an exclusive boundary (open+seconds); the policy accepts either when a
    whole frame uses one convention consistently.
    """
    snapshot = _snapshot()
    decision_at = snapshot.decision_at
    candles = {}
    for symbol, frames in snapshot.candles.items():
        candles[symbol] = {}
        for timeframe, rows in frames.items():
            current_bucket_open = (decision_at // timeframe.seconds) * timeframe.seconds
            final_open = current_bucket_open - timeframe.seconds
            first_open = final_open - (len(rows) - 1) * timeframe.seconds
            shifted = []
            for index, row in enumerate(rows):
                open_time = first_open + index * timeframe.seconds
                close_time = open_time + timeframe.seconds - (1 if inclusive_close else 0)
                shifted.append(replace(row, open_time=open_time, close_time=close_time, available_at=close_time))
            candles[symbol][timeframe] = tuple(shifted)
    return replace(snapshot, candles=candles)


def _replace_frame(snapshot, symbol, timeframe, rows):
    candles = {key: dict(frames) for key, frames in snapshot.candles.items()}
    candles[symbol][timeframe] = tuple(rows)
    return replace(snapshot, candles=candles)


def test_execution_stop_cannot_sit_inside_confirmed_h4_structure():
    allowed, reasons = _validate(_plan(stop=98.0), _context(invalidation=95.0))
    assert allowed is False
    assert "HARD_STOP_INSIDE_H4_STRUCTURE" in reasons


def test_structurally_wider_stop_is_allowed_by_policy_not_tightened_for_rr():
    allowed, reasons = _validate(_plan(stop=94.0), _context(invalidation=95.0))
    assert allowed is True
    assert reasons == ()


def test_lower_timeframe_setup_cannot_override_no_long_thesis():
    allowed, reasons = _validate(_plan(stop=94.0), _context(state=ThesisState.NO_LONG, invalidation=95.0))
    assert allowed is False
    assert "MEDIUM_HORIZON_LONG_NOT_ALLOWED" in reasons


def test_execution_validator_rejects_symbol_mismatch():
    allowed, reasons = _validate(_plan(stop=94.0, symbol="ETHUSDT"), _context(symbol="BTCUSDT"))
    assert allowed is False
    assert "PLAN_CONTEXT_SYMBOL_MISMATCH" in reasons


def test_execution_validator_rejects_decision_time_mismatch():
    allowed, reasons = _validate(
        _plan(stop=94.0, decision_at=DECISION_AT + 60),
        _context(),
        evaluated_at=DECISION_AT + 60,
    )
    assert allowed is False
    assert "PLAN_CONTEXT_DECISION_TIME_MISMATCH" in reasons


def test_execution_validator_never_revives_invalidated_plan():
    allowed, reasons = _validate(_plan(stop=94.0, state=PlanState.INVALIDATED), _context())
    assert allowed is False
    assert "EXECUTION_PLAN_INVALIDATED" in reasons


def test_execution_validator_rejects_observe_plan_as_non_actionable():
    allowed, reasons = _validate(_plan(stop=94.0, state=PlanState.OBSERVE), _context())
    assert allowed is False
    assert "EXECUTION_PLAN_NOT_ACTIONABLE" in reasons


def test_execution_validator_rejects_nonfinite_structural_invalidation():
    allowed, reasons = _validate(_plan(stop=94.0), _context(invalidation=float("nan")))
    assert allowed is False
    assert "STRUCTURAL_INVALIDATION_UNKNOWN" in reasons


def test_execution_validator_rejects_expired_ready_plan():
    plan = _plan(stop=94.0, state=PlanState.READY)
    allowed, reasons = _validate(plan, _context(), evaluated_at=plan.expires_at)
    assert allowed is False
    assert "EXECUTION_PLAN_EXPIRED" in reasons


def test_execution_validator_requires_evaluation_at_or_after_decision_cut():
    allowed, reasons = _validate(_plan(stop=94.0), _context(), evaluated_at=DECISION_AT - 1)
    assert allowed is False
    assert "INVALID_EXECUTION_EVALUATION_TIME" in reasons


def test_triggered_plan_is_not_reclassified_as_expired_entry_setup():
    plan = _plan(stop=94.0, state=PlanState.TRIGGERED)
    allowed, reasons = _validate(plan, _context(), evaluated_at=plan.expires_at + 1)
    assert allowed is True
    assert reasons == ()


def test_medium_horizon_accepts_real_mexc_inclusive_close_semantics():
    snapshot = _fresh_thesis_snapshot(inclusive_close=True)
    context = evaluate_medium_horizon_long(snapshot, "BTCUSDT")
    assert context.can_authorize_trade is False
    assert context.decision_at == snapshot.decision_at
    assert 0.0 < context.volatility_exposure_scalar <= 1.0


def test_medium_horizon_accepts_exclusive_replay_bucket_semantics():
    snapshot = _fresh_thesis_snapshot(inclusive_close=False)
    context = evaluate_medium_horizon_long(snapshot, "BTCUSDT")
    assert context.decision_at == snapshot.decision_at


def test_volatility_changes_exposure_scalar_not_structural_invalidation():
    base = evaluate_medium_horizon_long(_fresh_thesis_snapshot(), "BTCUSDT")
    assert base.structural_invalidation is None or base.structural_invalidation > 0
    assert base.volatility_exposure_scalar in {0.4, 0.5, 0.6, 0.8, 1.0}


def test_medium_horizon_rejects_future_or_open_daily_candle():
    snapshot = _fresh_thesis_snapshot()
    d1 = list(snapshot.candles["BTCUSDT"][TacticalTimeframe.D1])
    d1[-1] = replace(d1[-1], close_time=snapshot.decision_at + 60, available_at=snapshot.decision_at + 60)
    with pytest.raises(DataQualityError, match="future or open candle"):
        evaluate_medium_horizon_long(_replace_frame(snapshot, "BTCUSDT", TacticalTimeframe.D1, d1), "BTCUSDT")


def test_medium_horizon_rejects_stale_h4_frame():
    snapshot = _fresh_thesis_snapshot()
    shift = 10 * TacticalTimeframe.H4.seconds
    h4 = tuple(
        replace(row, open_time=row.open_time - shift, close_time=row.close_time - shift, available_at=row.available_at - shift)
        for row in snapshot.candles["BTCUSDT"][TacticalTimeframe.H4]
    )
    with pytest.raises(DataQualityError, match="latest candle is stale"):
        evaluate_medium_horizon_long(_replace_frame(snapshot, "BTCUSDT", TacticalTimeframe.H4, h4), "BTCUSDT")


def test_medium_horizon_rejects_missing_terminal_h4_bucket_even_if_age_limit_passes():
    snapshot = _fresh_thesis_snapshot()
    h4 = list(snapshot.candles["BTCUSDT"][TacticalTimeframe.H4])
    h4.pop()
    with pytest.raises(DataQualityError, match="latest completed bucket is missing"):
        evaluate_medium_horizon_long(_replace_frame(snapshot, "BTCUSDT", TacticalTimeframe.H4, h4), "BTCUSDT")


def test_medium_horizon_rejects_h4_cadence_gap():
    snapshot = _fresh_thesis_snapshot()
    h4 = list(snapshot.candles["BTCUSDT"][TacticalTimeframe.H4])
    del h4[-2]
    with pytest.raises(DataQualityError, match="cadence gap or overlap"):
        evaluate_medium_horizon_long(_replace_frame(snapshot, "BTCUSDT", TacticalTimeframe.H4, h4), "BTCUSDT")


def test_medium_horizon_rejects_h4_duration_mismatch():
    snapshot = _fresh_thesis_snapshot()
    h4 = list(snapshot.candles["BTCUSDT"][TacticalTimeframe.H4])
    last = h4[-1]
    h4[-1] = replace(last, close_time=last.close_time - 60, available_at=last.close_time - 60)
    with pytest.raises(DataQualityError, match="candle duration mismatch"):
        evaluate_medium_horizon_long(_replace_frame(snapshot, "BTCUSDT", TacticalTimeframe.H4, h4), "BTCUSDT")


def test_medium_horizon_rejects_shifted_non_utc_h4_buckets():
    snapshot = _fresh_thesis_snapshot()
    h4 = tuple(
        replace(row, open_time=row.open_time - 60, close_time=row.close_time - 60, available_at=row.available_at - 60)
        for row in snapshot.candles["BTCUSDT"][TacticalTimeframe.H4]
    )
    with pytest.raises(DataQualityError, match="not UTC bucket aligned"):
        evaluate_medium_horizon_long(_replace_frame(snapshot, "BTCUSDT", TacticalTimeframe.H4, h4), "BTCUSDT")


def test_medium_horizon_rejects_nonfinite_h4_ohlcv():
    snapshot = _fresh_thesis_snapshot()
    h4 = list(snapshot.candles["BTCUSDT"][TacticalTimeframe.H4])
    h4[-1] = replace(h4[-1], high=float("nan"))
    with pytest.raises(DataQualityError, match="non-finite OHLCV"):
        evaluate_medium_horizon_long(_replace_frame(snapshot, "BTCUSDT", TacticalTimeframe.H4, h4), "BTCUSDT")


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


def test_newly_closed_h4_break_below_protected_low_blocks_long(monkeypatch):
    snapshot = _fresh_thesis_snapshot()
    h4 = list(snapshot.candles["BTCUSDT"][TacticalTimeframe.H4])
    last = h4[-1]
    protected = last.low - 10.0
    h4[-1] = replace(last, open=protected + 5.0, high=protected + 10.0, low=protected - 2.0, close=protected - 1.0)
    snapshot = _replace_frame(snapshot, "BTCUSDT", TacticalTimeframe.H4, h4)
    calls = iter((
        (StructureState.BULLISH, ()),
        (StructureState.BULLISH, (
            SimpleNamespace(kind=SwingKind.LOW, price=protected, confirmed_at=h4[-2].close_time),
        )),
    ))
    monkeypatch.setattr(medium_horizon, "_structure", lambda candles, timeframe: next(calls))
    context = evaluate_medium_horizon_long(snapshot, "BTCUSDT")
    assert context.state is ThesisState.NO_LONG
    assert "H4_PROTECTED_LOW_CLOSE_BREAK" in context.reasons


def test_prior_right_edge_close_break_cannot_reactivate_after_latest_recovery(monkeypatch):
    snapshot = _fresh_thesis_snapshot()
    h4 = list(snapshot.candles["BTCUSDT"][TacticalTimeframe.H4])
    protected = h4[-3].low - 10.0
    penultimate = h4[-2]
    latest = h4[-1]
    h4[-2] = replace(
        penultimate,
        open=protected + 5.0,
        high=protected + 10.0,
        low=protected - 2.0,
        close=protected - 1.0,
    )
    h4[-1] = replace(
        latest,
        open=protected + 1.0,
        high=protected + 8.0,
        low=protected + 0.5,
        close=protected + 4.0,
    )
    snapshot = _replace_frame(snapshot, "BTCUSDT", TacticalTimeframe.H4, h4)
    calls = iter((
        (StructureState.BULLISH, ()),
        (StructureState.BULLISH, (
            SimpleNamespace(kind=SwingKind.LOW, price=protected, confirmed_at=h4[-3].close_time),
        )),
    ))
    monkeypatch.setattr(medium_horizon, "_structure", lambda candles, timeframe: next(calls))
    context = evaluate_medium_horizon_long(snapshot, "BTCUSDT")
    assert context.state is ThesisState.NO_LONG
    assert "H4_PROTECTED_LOW_CLOSE_BREAK" in context.reasons


def test_h4_wick_below_protected_low_is_observe_not_new_long(monkeypatch):
    snapshot = _fresh_thesis_snapshot()
    h4 = list(snapshot.candles["BTCUSDT"][TacticalTimeframe.H4])
    last = h4[-1]
    protected = last.low - 10.0
    h4[-1] = replace(last, open=protected + 3.0, high=protected + 10.0, low=protected - 2.0, close=protected + 5.0)
    snapshot = _replace_frame(snapshot, "BTCUSDT", TacticalTimeframe.H4, h4)
    calls = iter((
        (StructureState.BULLISH, ()),
        (StructureState.BULLISH, (
            SimpleNamespace(kind=SwingKind.LOW, price=protected, confirmed_at=h4[-2].close_time),
        )),
    ))
    monkeypatch.setattr(medium_horizon, "_structure", lambda candles, timeframe: next(calls))
    context = evaluate_medium_horizon_long(snapshot, "BTCUSDT")
    assert context.state is ThesisState.OBSERVE
    assert "H4_PROTECTED_LOW_WICK_BREACH" in context.reasons
