from __future__ import annotations

import math

import pytest

from trading.research.metrics import (
    grouped_performance,
    max_consecutive_losses,
    max_drawdown_pct,
    percentile,
    performance_report,
    sample_quality,
)


def test_report_core_metrics_on_known_series():
    returns = [2.0, -1.0, 3.0, -1.0, -1.0, 4.0]
    report = performance_report(returns, span_days=365.0)
    assert report.n_trades == 6
    assert report.expectancy_pct == pytest.approx(1.0)
    assert report.win_rate == pytest.approx(0.5)
    assert report.avg_win_pct == pytest.approx(3.0)
    assert report.avg_loss_pct == pytest.approx(-1.0)
    assert report.payoff_ratio == pytest.approx(3.0)
    assert report.profit_factor == pytest.approx(9.0 / 3.0)
    assert report.max_drawdown_pct == pytest.approx(-2.0)
    assert report.max_consecutive_losses == 2
    assert report.trades_per_year == pytest.approx(6.0)
    assert report.calmar_ratio == pytest.approx(6.0 / 2.0)


def test_profit_factor_is_undefined_without_losses_instead_of_infinite():
    report = performance_report([1.0, 2.0, 0.5], span_days=30.0)
    assert report.profit_factor is None
    assert report.sample_quality == "INSUFFICIENT"


def test_tail_loss_requires_minimum_sample():
    small = performance_report([1.0, -2.0] * 5, span_days=100.0)
    assert small.tail_loss_p5_pct is None
    large = performance_report([1.0, -2.0, 0.5, -0.5] * 10, span_days=100.0)
    assert large.tail_loss_p5_pct == pytest.approx(-2.0)
    assert large.expected_shortfall_p5_pct == pytest.approx(-2.0)


def test_non_finite_returns_are_rejected():
    with pytest.raises(ValueError):
        performance_report([1.0, math.nan], span_days=10.0)
    with pytest.raises(ValueError):
        performance_report([1.0, math.inf], span_days=10.0)


def test_empty_series_is_not_reported_as_healthy():
    report = performance_report([], span_days=10.0)
    assert report.n_trades == 0
    assert report.sample_quality == "INSUFFICIENT"
    assert report.profit_factor is None


def test_helpers():
    assert max_drawdown_pct([1.0, -3.0, 1.0, -1.0]) == pytest.approx(-3.0)
    assert max_consecutive_losses([1, -1, 0, -2, 3, -1]) == 3
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
    assert sample_quality(29) == "INSUFFICIENT"
    assert sample_quality(30) == "SMALL"
    assert sample_quality(100) == "ADEQUATE"


def test_grouped_performance_uses_common_span():
    class T:
        def __init__(self, net, entry, group):
            self.net_return_pct = net
            self.entry_time = entry
            self.exit_time = entry + 1
            self.group = group

    trades = [T(1.0, 0, "A"), T(-1.0, 1, "B"), T(2.0, 2, "A")]
    groups = grouped_performance(trades, key=lambda t: t.group, span_days=365.0)
    assert set(groups) == {"A", "B"}
    assert groups["A"].n_trades == 2
    assert groups["A"].trades_per_year == pytest.approx(2.0)
