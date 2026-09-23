"""Tests for trading.backtest.benchmark (B&H metrics + GO/NO-GO verdict)."""

from __future__ import annotations

import math

import pytest

from trading.backtest.benchmark import (
    BuyHoldMetrics,
    GoNoGoVerdict,
    compute_buy_and_hold,
    evaluate_go_no_go,
)
from trading.data.binance_perp import Candle, FundingRow


def _daily(closes: list[float], start: int = 0) -> list[Candle]:
    day = 86_400
    out: list[Candle] = []
    for i, close in enumerate(closes):
        open_time = start + i * day
        close_time = open_time + day - 1
        out.append(
            Candle(
                open_time=open_time,
                close_time=close_time,
                available_at=close_time,
                open=close,
                high=close * 1.02,
                low=close * 0.98,
                close=close,
                volume=1.0,
                quote_volume=close,
            )
        )
    return out


def test_compute_buy_and_hold_rejects_short_series():
    with pytest.raises(ValueError):
        compute_buy_and_hold(daily=_daily([100.0]))


def test_compute_buy_and_hold_returns_positive_on_uptrend():
    closes = [100.0 * math.exp(0.001 * i) for i in range(365)]
    metrics = compute_buy_and_hold(daily=_daily(closes))
    assert metrics.gross_return_pct > 30.0  # ~44% raw
    assert metrics.sharpe_annualized > 0
    assert metrics.max_drawdown_pct <= 0
    assert metrics.entry_price == pytest.approx(closes[0])
    assert metrics.exit_price == pytest.approx(closes[-1])
    assert metrics.n_daily_bars == 365


def test_compute_buy_and_hold_applies_funding_cost():
    closes = [100.0] * 100  # flat -> zero gross
    daily = _daily(closes)
    # long-side funding drag: 3 accruals inside window, each 0.01%
    funding = [
        FundingRow(
            symbol="BTCUSDT",
            funding_time=daily[10].close_time + 3600,
            available_at=daily[10].close_time + 3600,
            funding_rate=0.0001,
        ),
        FundingRow(
            symbol="BTCUSDT",
            funding_time=daily[20].close_time + 3600,
            available_at=daily[20].close_time + 3600,
            funding_rate=0.0001,
        ),
        FundingRow(
            symbol="BTCUSDT",
            funding_time=daily[30].close_time + 3600,
            available_at=daily[30].close_time + 3600,
            funding_rate=0.0001,
        ),
    ]
    without_funding = compute_buy_and_hold(daily=daily, funding=None)
    with_funding = compute_buy_and_hold(daily=daily, funding=funding)
    # funding_pct should be 3 * 0.01 = 0.03
    assert with_funding.cost.funding_pct == pytest.approx(0.03)
    assert with_funding.cost.total_pct > without_funding.cost.total_pct


def test_compute_buy_and_hold_max_dd_negative_on_pullback():
    # up 20%, down 15%, up 5%
    closes = [100.0, 105.0, 110.0, 115.0, 120.0, 118.0, 112.0, 108.0, 102.0, 105.0]
    metrics = compute_buy_and_hold(daily=_daily(closes))
    assert metrics.max_drawdown_pct < 0


def test_evaluate_go_no_go_all_checks_pass():
    benchmark = BuyHoldMetrics(
        n_daily_bars=365,
        entry_time=0,
        exit_time=364 * 86_400,
        entry_price=100.0,
        exit_price=150.0,
        gross_return_pct=50.0,
        cost=_zero_cost(),
        net_return_pct=49.88,
        daily_return_stdev_pct=1.5,
        sharpe_annualized=0.6,
        max_drawdown_pct=-30.0,
        holding_days=364.0,
    )
    verdict = evaluate_go_no_go(
        strategy_sharpe=1.2,
        strategy_max_dd_pct=-15.0,  # 15% < 60% of 30 = 18%
        benchmark=benchmark,
        fold_sharpes=[0.7, 0.9, 0.8, 0.6],
    )
    assert verdict.sharpe_above_0_8 is True
    assert verdict.beats_benchmark_sharpe is True
    assert verdict.drawdown_below_60_pct_of_benchmark is True
    assert verdict.consistency_ok is True
    assert verdict.overall_go is True


def test_evaluate_go_no_go_fails_on_low_sharpe():
    benchmark = _sample_benchmark()
    verdict = evaluate_go_no_go(
        strategy_sharpe=0.5,
        strategy_max_dd_pct=-10.0,
        benchmark=benchmark,
        fold_sharpes=[0.6, 0.7, 0.8],
    )
    assert verdict.sharpe_above_0_8 is False
    assert verdict.overall_go is False


def test_evaluate_go_no_go_fails_on_dd_above_60_pct_of_benchmark():
    benchmark = _sample_benchmark()  # max DD = -30%
    verdict = evaluate_go_no_go(
        strategy_sharpe=1.2,
        strategy_max_dd_pct=-25.0,  # 25% > 60% of 30 = 18%
        benchmark=benchmark,
        fold_sharpes=[0.9, 0.9, 0.9, 0.9],
    )
    assert verdict.drawdown_below_60_pct_of_benchmark is False
    assert verdict.overall_go is False


def test_evaluate_go_no_go_fails_when_only_2_of_4_folds_consistent():
    benchmark = _sample_benchmark()
    verdict = evaluate_go_no_go(
        strategy_sharpe=1.2,
        strategy_max_dd_pct=-10.0,
        benchmark=benchmark,
        fold_sharpes=[0.9, 0.9, 0.3, 0.2],
    )
    assert verdict.consistency_folds_above_0_5 == 2
    assert verdict.consistency_ok is False
    assert verdict.overall_go is False


def test_evaluate_go_no_go_fails_when_below_benchmark_sharpe():
    benchmark = BuyHoldMetrics(
        n_daily_bars=365,
        entry_time=0,
        exit_time=364 * 86_400,
        entry_price=100.0,
        exit_price=150.0,
        gross_return_pct=50.0,
        cost=_zero_cost(),
        net_return_pct=49.88,
        daily_return_stdev_pct=1.5,
        sharpe_annualized=1.5,  # very strong bull run
        max_drawdown_pct=-10.0,
        holding_days=364.0,
    )
    verdict = evaluate_go_no_go(
        strategy_sharpe=1.0,  # strategy < benchmark
        strategy_max_dd_pct=-3.0,  # 3% < 6% = ok
        benchmark=benchmark,
        fold_sharpes=[0.9, 0.9, 0.9, 0.9],
    )
    assert verdict.beats_benchmark_sharpe is False
    assert verdict.overall_go is False


def _zero_cost():
    from trading.backtest.cost_model import CostBreakdown

    return CostBreakdown(taker_fee_pct=0.08, funding_pct=0.0, slippage_pct=0.04)


def _sample_benchmark() -> BuyHoldMetrics:
    return BuyHoldMetrics(
        n_daily_bars=365,
        entry_time=0,
        exit_time=364 * 86_400,
        entry_price=100.0,
        exit_price=150.0,
        gross_return_pct=50.0,
        cost=_zero_cost(),
        net_return_pct=49.88,
        daily_return_stdev_pct=1.5,
        sharpe_annualized=0.6,
        max_drawdown_pct=-30.0,
        holding_days=364.0,
    )
