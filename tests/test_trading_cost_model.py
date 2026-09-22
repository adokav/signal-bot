"""Tests for trading.backtest.cost_model."""

from __future__ import annotations

import pytest

from trading.backtest.cost_model import (
    CostBreakdown,
    accrued_funding_pct,
    round_trip_cost_pct,
    slippage_round_trip_pct,
    taker_fee_round_trip_pct,
)
from trading.data.binance_perp import FundingRow


def _funding(times_rates):
    return [
        FundingRow(symbol="BTCUSDT", funding_time=t, available_at=t, funding_rate=r)
        for t, r in times_rates
    ]


def test_taker_fee_round_trip_is_two_sided():
    assert taker_fee_round_trip_pct(taker_fee_bps=4.0) == pytest.approx(0.08)
    assert taker_fee_round_trip_pct(taker_fee_bps=0.0) == 0.0


def test_slippage_round_trip_is_two_sided():
    assert slippage_round_trip_pct(slippage_bps=2.0) == pytest.approx(0.04)


def test_funding_accrual_sums_intervals_inside_window():
    funding = _funding([
        (1_000, 0.0001),
        (2_000, -0.0002),
        (3_000, 0.0003),
        (4_000, 0.0004),
    ])
    # entry_time=1_500, exit_time=3_500 -> hits t=2_000 and t=3_000 only
    result = accrued_funding_pct(funding, entry_time=1_500, exit_time=3_500)
    assert result == pytest.approx((0.0003 - 0.0002) * 100.0)


def test_funding_accrual_excludes_entry_boundary_includes_exit_boundary():
    funding = _funding([(1_000, 0.0001), (2_000, 0.0002)])
    # window (1_000, 2_000] includes only t=2_000, not t=1_000
    result = accrued_funding_pct(funding, entry_time=1_000, exit_time=2_000)
    assert result == pytest.approx(0.02)


def test_funding_accrual_rejects_invalid_window():
    with pytest.raises(ValueError):
        accrued_funding_pct([], entry_time=100, exit_time=100)
    with pytest.raises(ValueError):
        accrued_funding_pct([], entry_time=200, exit_time=100)
    with pytest.raises(ValueError):
        accrued_funding_pct([], entry_time=-1, exit_time=100)


def test_round_trip_cost_composes_all_three_parts():
    funding = _funding([(1_500, 0.0001)])
    breakdown = round_trip_cost_pct(
        entry_time=1_000,
        exit_time=2_000,
        funding_history=funding,
        taker_fee_bps=4.0,
        slippage_bps=2.0,
    )
    assert isinstance(breakdown, CostBreakdown)
    assert breakdown.taker_fee_pct == pytest.approx(0.08)
    assert breakdown.slippage_pct == pytest.approx(0.04)
    assert breakdown.funding_pct == pytest.approx(0.01)
    assert breakdown.total_pct == pytest.approx(0.13)


def test_round_trip_cost_without_funding_history_underestimates():
    breakdown = round_trip_cost_pct(
        entry_time=1_000,
        exit_time=2_000,
        funding_history=None,
    )
    assert breakdown.funding_pct == 0.0
    assert breakdown.total_pct == pytest.approx(0.12)  # 8bps + 4bps
