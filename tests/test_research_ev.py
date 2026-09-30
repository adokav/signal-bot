from __future__ import annotations

import math

import pytest

from trading.research.expected_value import CostEstimate, expected_value


COSTS = CostEstimate(fee_pct=0.08, spread_pct=0.02, slippage_pct=0.04, funding_pct=0.03)


def test_ev_formula_and_breakeven():
    ev = expected_value(
        p_win=0.55, avg_win_pct=3.0, avg_loss_pct=2.0, costs=COSTS,
        probability_is_calibrated=True, p_win_lower=0.52,
    )
    assert ev.gross_ev_pct == pytest.approx(0.55 * 3.0 - 0.45 * 2.0)
    assert ev.cost_pct == pytest.approx(0.17)
    assert ev.net_ev_pct == pytest.approx(0.75 - 0.17)
    assert ev.breakeven_probability == pytest.approx((2.0 + 0.17) / 5.0)
    assert ev.passes
    assert ev.can_authorize_trade is False


def test_raw_model_confidence_never_passes():
    ev = expected_value(
        p_win=0.83, avg_win_pct=3.0, avg_loss_pct=2.0, costs=COSTS,
        probability_is_calibrated=False, p_win_lower=0.80,
    )
    assert not ev.passes
    assert "UNCALIBRATED_PROBABILITY" in ev.reasons


def test_tiny_positive_ev_is_rejected_by_safety_margin():
    ev = expected_value(
        p_win=0.5, avg_win_pct=2.0, avg_loss_pct=1.6, costs=COSTS,
        probability_is_calibrated=True, p_win_lower=0.5,
    )
    assert 0 < ev.net_ev_pct < ev.min_net_ev_pct
    assert "NET_EV_BELOW_SAFETY_MARGIN" in ev.reasons


def test_sampling_uncertainty_can_veto_positive_point_ev():
    ev = expected_value(
        p_win=0.55, avg_win_pct=3.0, avg_loss_pct=2.0, costs=COSTS,
        probability_is_calibrated=True, p_win_lower=0.40,
    )
    assert ev.net_ev_pct > 0
    assert "NET_EV_NOT_POSITIVE_AT_LOWER_BOUND" in ev.reasons
    unknown = expected_value(
        p_win=0.55, avg_win_pct=3.0, avg_loss_pct=2.0, costs=COSTS,
        probability_is_calibrated=True,
    )
    assert "PROBABILITY_UNCERTAINTY_UNKNOWN" in unknown.reasons


def test_invalid_inputs_are_rejected():
    with pytest.raises(ValueError):
        expected_value(p_win=1.2, avg_win_pct=1, avg_loss_pct=1, costs=COSTS, probability_is_calibrated=True)
    with pytest.raises(ValueError):
        expected_value(p_win=0.5, avg_win_pct=math.nan, avg_loss_pct=1, costs=COSTS, probability_is_calibrated=True)
    with pytest.raises(ValueError):
        expected_value(p_win=0.5, avg_win_pct=1, avg_loss_pct=-1, costs=COSTS, probability_is_calibrated=True)
    with pytest.raises(ValueError):
        CostEstimate(fee_pct=-0.1, spread_pct=0, slippage_pct=0, funding_pct=0)
