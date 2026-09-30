from __future__ import annotations

import math

import pytest

from trading.risk.candidate import CandidateReport, render_evidence_lines
from trading.risk.checks import Check, CheckStatus, failed, passed, unknown
from trading.risk.drawdown import current_drawdown_pct, drawdown_risk_multiplier
from trading.risk.kill_switch import (
    REQUIRED_ACTIONS,
    KillSwitchInputs,
    KillSwitchLatch,
    kill_switch_reasons,
)
from trading.risk.portfolio import (
    PortfolioState,
    Position,
    evaluate_new_position,
    summarize,
)
from trading.risk.sizing import SizingLimits, position_size
from trading.risk.trade_gate import GATE_QUESTIONS, GateDecision, evaluate_gate


# --- sizing ----------------------------------------------------------------


def test_size_is_risk_budget_over_stop_distance_including_costs():
    decision = position_size(
        equity=10_000, entry_price=100.0, stop_price=98.0, round_trip_cost_pct=0.2,
        quote_volume_24h=1e9, risk_multiplier=1.0,
    )
    # risk 0.5% of 10k = 50; stop distance 2% + 0.2% cost = 2.2%
    assert decision.binding_constraint == "RISK_BUDGET"
    assert decision.notional == pytest.approx(50 / 0.022)
    assert decision.risk_amount == pytest.approx(50.0)
    assert decision.can_authorize_trade is False


def test_size_respects_leverage_and_liquidity_caps():
    tight_stop = position_size(
        equity=10_000, entry_price=100.0, stop_price=99.9, round_trip_cost_pct=0.0,
        quote_volume_24h=1e9, risk_multiplier=1.0,
    )
    assert tight_stop.binding_constraint == "LEVERAGE_CAP"
    assert tight_stop.notional == pytest.approx(10_000)
    thin = position_size(
        equity=10_000, entry_price=100.0, stop_price=98.0, round_trip_cost_pct=0.0,
        quote_volume_24h=1_000_000, risk_multiplier=1.0,
    )
    assert thin.binding_constraint == "LIQUIDITY_CAP"
    assert thin.notional == pytest.approx(1_000)


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        (dict(quote_volume_24h=None), "LIQUIDITY_UNKNOWN"),
        (dict(stop_price=101.0), "INVALID_STOP_GEOMETRY"),
        (dict(entry_price=math.nan), "NON_FINITE_INPUT"),
        (dict(risk_multiplier=0.0), "RISK_MULTIPLIER_ZERO"),
        (dict(risk_multiplier=1.5), "INVALID_RISK_MULTIPLIER"),
    ],
)
def test_unusable_inputs_size_to_zero(kwargs, reason):
    base = dict(
        equity=10_000, entry_price=100.0, stop_price=98.0, round_trip_cost_pct=0.1,
        quote_volume_24h=1e9, risk_multiplier=1.0,
    )
    base.update(kwargs)
    decision = position_size(**base)
    assert decision.notional == 0.0
    assert reason in decision.reasons


def test_sizing_limits_have_hard_ceilings():
    with pytest.raises(ValueError):
        SizingLimits(risk_per_trade_pct=5.0)
    with pytest.raises(ValueError):
        SizingLimits(max_notional_multiple=10.0)


# --- drawdown ladder --------------------------------------------------------


@pytest.mark.parametrize(
    "dd, multiplier, state",
    [(0.0, 1.0, "NORMAL"), (2.9, 1.0, "NORMAL"), (3.0, 0.75, "REDUCED"),
     (6.0, 0.5, "REDUCED"), (9.9, 0.25, "REDUCED"), (10.0, 0.0, "HALTED"),
     (-12.0, 0.0, "HALTED")],
)
def test_drawdown_ladder(dd, multiplier, state):
    risk = drawdown_risk_multiplier(dd)
    assert risk.multiplier == multiplier
    assert risk.state == state


def test_unknown_drawdown_halts_and_martingale_ladders_are_refused():
    assert drawdown_risk_multiplier(None).multiplier == 0.0
    assert drawdown_risk_multiplier(math.nan).state == "UNKNOWN"
    with pytest.raises(ValueError, match="must not grow"):
        drawdown_risk_multiplier(4.0, ladder=((3.0, 0.5), (5.0, 1.0)))


def test_current_drawdown_from_equity_curve():
    assert current_drawdown_pct([100, 120, 108]) == pytest.approx(10.0)
    with pytest.raises(ValueError):
        current_drawdown_pct([100, -1])


# --- portfolio --------------------------------------------------------------


def _state(positions=(), **overrides):
    values = dict(equity=10_000.0, positions=tuple(positions), daily_pnl_pct=-0.5,
                  weekly_pnl_pct=-1.0, drawdown_pct=2.0)
    values.update(overrides)
    return PortfolioState(**values)


def test_unknown_correlation_is_treated_as_correlated():
    held = [Position("SOLUSDT", 3_000, "L1", 1.3), Position("AVAXUSDT", 3_000, "L1", 1.4)]
    candidate = Position("ETHFIUSDT", 2_000, "LRT", 1.5)
    checks = {c.key: c for c in evaluate_new_position(
        _state(held), candidate, correlation=lambda a, b: None,
    )}
    assert checks["correlated_exposure"].status is CheckStatus.FAIL
    assert "korele sayıldı" in checks["correlated_exposure"].detail
    uncorrelated = {c.key: c for c in evaluate_new_position(
        _state(held), candidate, correlation=lambda a, b: 0.1,
    )}
    assert uncorrelated["correlated_exposure"].status is CheckStatus.PASS


def test_unknown_beta_and_unknown_pnl_are_unknown_not_pass():
    checks = {c.key: c for c in evaluate_new_position(
        _state(daily_pnl_pct=None), Position("BTCUSDT", 1_000, "L1", None),
        correlation=lambda a, b: 1.0,
    )}
    assert checks["btc_beta"].status is CheckStatus.UNKNOWN
    assert checks["daily_loss"].status is CheckStatus.UNKNOWN
    assert summarize(tuple(checks.values())) is CheckStatus.UNKNOWN


def test_limits_fail_when_exceeded():
    checks = {c.key: c for c in evaluate_new_position(
        _state([Position("BTCUSDT", 9_000, "L1", 1.0)], daily_pnl_pct=-2.5),
        Position("ETHUSDT", 2_000, "L1", 1.1),
        correlation=lambda a, b: 0.85,
    )}
    assert checks["gross_exposure"].status is CheckStatus.FAIL
    assert checks["sector"].status is CheckStatus.FAIL
    assert checks["daily_loss"].status is CheckStatus.FAIL
    assert evaluate_new_position(
        _state(equity=0.0), Position("BTCUSDT", 1.0, "L1", 1.0), correlation=lambda a, b: 1.0
    )[0].status is CheckStatus.UNKNOWN


# --- kill switch ------------------------------------------------------------


def _healthy(**overrides) -> KillSwitchInputs:
    values = dict(
        now=1_000, last_market_data_at=990, data_source_healthy=True,
        exchange_state_matches=True, spread_bps=3.0, recent_slippage_bps=4.0,
        duplicate_order_detected=False, daily_pnl_pct=-0.3, drawdown_pct=1.0,
        recent_execution_failures=0, volatility_percentile=0.5,
    )
    values.update(overrides)
    return KillSwitchInputs(**values)


def test_kill_switch_clear_when_all_healthy():
    assert kill_switch_reasons(_healthy()) == ()
    assert KillSwitchLatch().evaluate(_healthy()) == ()


@pytest.mark.parametrize(
    "overrides, reason",
    [
        (dict(last_market_data_at=500), "STALE_MARKET_DATA"),
        (dict(last_market_data_at=None), "MARKET_DATA_UNKNOWN"),
        (dict(exchange_state_matches=False), "EXCHANGE_STATE_MISMATCH"),
        (dict(exchange_state_matches=None), "EXCHANGE_STATE_UNKNOWN"),
        (dict(spread_bps=40.0), "ABNORMAL_SPREAD"),
        (dict(recent_slippage_bps=math.nan), "SLIPPAGE_UNKNOWN"),
        (dict(duplicate_order_detected=True), "DUPLICATE_ORDER"),
        (dict(daily_pnl_pct=-2.5), "DAILY_LOSS_LIMIT"),
        (dict(drawdown_pct=-10.5), "DRAWDOWN_LIMIT"),
        (dict(recent_execution_failures=5), "REPEATED_EXECUTION_FAILURES"),
        (dict(volatility_percentile=0.999), "UNTESTED_EXTREME_VOLATILITY"),
        (dict(data_source_healthy=None), "DATA_SOURCE_UNKNOWN"),
    ],
)
def test_kill_switch_trips(overrides, reason):
    assert reason in kill_switch_reasons(_healthy(**overrides))


def test_latch_requires_ack_and_clear_conditions_to_reset():
    latch = KillSwitchLatch()
    assert latch.evaluate(_healthy(spread_bps=40.0)) == REQUIRED_ACTIONS
    # Conditions clearing on their own do not re-arm the system.
    assert latch.evaluate(_healthy()) == REQUIRED_ACTIONS
    assert not latch.reset(operator_ack="", inputs=_healthy())
    assert not latch.reset(operator_ack="checked", inputs=_healthy(spread_bps=40.0))
    assert latch.reset(operator_ack="spread normalized, checked book", inputs=_healthy())
    assert latch.evaluate(_healthy()) == ()
    assert [h["event"] for h in latch.history] == ["TRIPPED", "RESET"]


# --- trade gate -------------------------------------------------------------


def _all_pass() -> dict[str, Check]:
    return {key: passed(key, "ok") for key in GATE_QUESTIONS}


def test_all_twelve_pass_is_execution_ready_but_still_not_authorized():
    decision = evaluate_gate("btcusdt", _all_pass())
    assert decision.status == "EXECUTION_READY"
    assert decision.no_trade is False
    assert decision.can_authorize_trade is False
    assert decision.to_dict()["can_authorize_trade"] is False
    with pytest.raises(ValueError):
        GateDecision(
            symbol="X", status="WATCH", no_trade=True, checks=(), reasons=(),
            edge_observable=CheckStatus.PASS, edge_validated=CheckStatus.PASS,
            reward_sufficient=CheckStatus.PASS, can_authorize_trade=True,
        )


def test_missing_answers_are_unknown_and_cap_status_at_watch():
    answers = {k: passed(k, "ok") for k in ("data_fresh", "liquid_enough", "regime_compatible", "stop_at_invalidation")}
    decision = evaluate_gate("ETHUSDT", answers)
    assert decision.status == "WATCH"
    assert decision.no_trade
    assert "oos_expectancy_positive:UNKNOWN" in decision.reasons
    assert decision.edge_validated is CheckStatus.UNKNOWN


def test_high_confidence_without_calibration_stays_watch():
    answers = _all_pass()
    answers["probabilities_calibrated"] = unknown("probabilities_calibrated", "model %83 diyor, kalibrasyon yok")
    assert evaluate_gate("SOLUSDT", answers).status == "WATCH"


@pytest.mark.parametrize("key", ["data_fresh", "liquid_enough", "stop_at_invalidation", "oos_expectancy_positive", "net_ev_positive"])
def test_disqualifying_failures_reject(key):
    answers = _all_pass()
    answers[key] = failed(key, "no")
    decision = evaluate_gate("X", answers)
    assert decision.status == "REJECT"
    assert decision.no_trade


def test_readiness_failure_leaves_candidate_qualified():
    answers = _all_pass()
    answers["execution_healthy"] = failed("execution_healthy", "execution engine yok")
    decision = evaluate_gate("X", answers)
    assert decision.status == "QUALIFIED"
    assert decision.no_trade


def test_gate_refuses_unknown_or_mismatched_keys():
    with pytest.raises(ValueError):
        evaluate_gate("X", {"vibes": passed("vibes", "ok")})
    with pytest.raises(ValueError):
        evaluate_gate("X", {"data_fresh": passed("liquid_enough", "ok")})


# --- candidate output -------------------------------------------------------


def test_candidate_renders_missing_evidence_as_missing():
    candidate = CandidateReport(
        symbol="BTCUSDT", status="WATCH", market_regime="UP_NORMAL_VOL",
        setup_type="TREND_PULLBACK", quant_score=None,
        calibrated_success_probability=None, expected_return_pct=None,
        expected_loss_pct=None, expected_value_pct=None, entry_zone=(100.0, 101.0),
        invalidation_level=99.0, stop=98.5, target_logic="1H direnç bölgesi",
        estimated_fees_pct=0.08, estimated_slippage_pct=0.04,
        expected_holding_period_hours=None, position_risk_pct=None,
        correlation_risk=None, confidence_quality="UNCALIBRATED", data_quality="FRESH",
    )
    text = "\n".join(render_evidence_lines(candidate))
    assert "YOK (kalibre edilmedi)" in text
    assert "YOK (tarihsel sonuç yok)" in text
    assert "50%" not in text
    assert candidate.to_dict()["entry_zone"] == [100.0, 101.0]
    with pytest.raises(ValueError):
        CandidateReport(**{**candidate.to_dict(), "entry_zone": None, "stop": math.inf, "gate_reasons": ()})
