"""Tests for the walk-forward backtest harness."""

from __future__ import annotations

import math

import pytest

from trading.backtest.walk_forward import (
    _walk_exit,
    compute_fold_metrics,
    enumerate_folds,
    run_backtest,
    simulate_trade,
)
from trading.data.binance_perp import Candle, FundingRow
from trading.strategies.tsmom import ExitPlan, TsmomParams


def _daily(closes: list[float], start: int = 0) -> list[Candle]:
    day = 86_400
    out = []
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


def _hourly(start_time: int, prices: list[float]) -> list[Candle]:
    hour = 3600
    out = []
    for i, price in enumerate(prices):
        open_time = start_time + i * hour
        close_time = open_time + hour - 1
        out.append(
            Candle(
                open_time=open_time,
                close_time=close_time,
                available_at=close_time,
                open=price,
                high=price * 1.005,
                low=price * 0.995,
                close=price,
                volume=1.0,
                quote_volume=price,
            )
        )
    return out


def _plan(entry: float, atr_pct: float = 2.0) -> ExitPlan:
    return ExitPlan(
        entry_price=entry,
        hard_stop=entry * (1 - 0.02),
        target_1=entry * (1 + atr_pct / 100.0),
        target_2=entry * (1 + 2 * atr_pct / 100.0),
        time_stop_seconds=48 * 3600,
    )


def test_walk_exit_hits_stop_before_target_1():
    plan = _plan(100.0)
    hourly = _hourly(0, [99.5, 98.5, 97.0, 96.0])
    _, exit_price, reason, mfe, mae = _walk_exit(hourly, plan, entry_time=0)
    assert reason == "STOP"
    assert exit_price == pytest.approx(plan.hard_stop)
    assert mae < 0


def test_walk_exit_target_1_then_target_2():
    plan = _plan(100.0, atr_pct=2.0)
    # rise to T1 (102), pull back to breakeven, run to T2 (104)
    hourly = _hourly(0, [101.0, 102.5, 101.0, 103.5, 104.5])
    _, exit_price, reason, _, _ = _walk_exit(hourly, plan, entry_time=0)
    assert reason == "TARGET_2"
    # weighted exit: (102 + 104) / 2 = 103
    assert exit_price == pytest.approx((plan.target_1 + plan.target_2) / 2.0)


def test_walk_exit_target_1_then_residual_breakeven():
    plan = _plan(100.0, atr_pct=2.0)
    # rise to T1, then drift to entry -> residual stopped at entry
    hourly = _hourly(0, [101.0, 102.5, 101.0, 100.0, 99.9])
    _, exit_price, reason, _, _ = _walk_exit(hourly, plan, entry_time=0)
    assert reason == "RESIDUAL_STOP_BREAKEVEN"
    # weighted: (102 + 100) / 2 = 101
    assert exit_price == pytest.approx((plan.target_1 + plan.entry_price) / 2.0)


def test_walk_exit_time_stop_fires_when_horizon_exhausted():
    plan = _plan(100.0)
    plan = ExitPlan(
        entry_price=plan.entry_price,
        hard_stop=plan.hard_stop,
        target_1=plan.target_1,
        target_2=plan.target_2,
        time_stop_seconds=3 * 3600,
    )
    # sideways, never hits stop or target; time_stop fires on the first bar
    # whose (close_time - entry_time) >= time_stop_seconds. entry_time=0,
    # bar 2 close=10799 (< 10800), bar 3 close=14399 (>= 10800) → fires here.
    hourly = _hourly(0, [100.5, 100.7, 100.9, 100.6])
    _, exit_price, reason, _, _ = _walk_exit(hourly, plan, entry_time=0)
    assert reason == "TIME_STOP"
    assert exit_price == pytest.approx(100.6)


def test_simulate_trade_subtracts_costs_from_gross_return():
    plan = _plan(100.0, atr_pct=2.0)
    hourly = _hourly(0, [101.0, 102.5, 101.0, 103.5, 104.5])
    funding = [
        FundingRow(symbol="BTCUSDT", funding_time=1800, available_at=1800, funding_rate=0.0001),
    ]
    trade = simulate_trade(
        symbol="BTCUSDT",
        plan=plan,
        scale=1.0,
        entry_time=0,
        hourly_after_entry=hourly,
        funding_history=funding,
        taker_fee_bps=4.0,
        slippage_bps=2.0,
    )
    assert trade.exit_reason == "TARGET_2"
    assert trade.gross_return_pct > 0
    assert trade.cost.total_pct > 0
    assert trade.net_return_pct == pytest.approx(
        trade.gross_return_pct - trade.cost.total_pct
    )


def test_enumerate_folds_leaves_embargo_between_train_and_test():
    folds = enumerate_folds(total_days=1000, n_folds=4, embargo_days=5, min_train_days=100)
    for fold in folds:
        assert fold.test_start_idx - fold.train_end_idx == 5


def test_enumerate_folds_returns_empty_when_history_too_short():
    folds = enumerate_folds(total_days=50, n_folds=4, embargo_days=5, min_train_days=100)
    assert folds == []


def test_compute_fold_metrics_positive_expectancy():
    # 5 trades: +2, +1, -0.5, +3, -1
    class _Trade:
        def __init__(self, net, gross, cost, mfe, mae, entry_time, exit_time, reason):
            self.net_return_pct = net
            self.gross_return_pct = gross

            class _C:
                def __init__(self, total):
                    self.total_pct = total

            self.cost = _C(cost)
            self.mfe_pct = mfe
            self.mae_pct = mae
            self.entry_time = entry_time
            self.exit_time = exit_time
            self.exit_reason = reason

    trades = [
        _Trade(2.0, 2.1, 0.1, 3.0, -0.5, 0, 86_400, "TARGET_2"),
        _Trade(1.0, 1.1, 0.1, 1.5, -0.3, 100_000, 100_000 + 86_400, "TARGET_2"),
        _Trade(-0.5, -0.4, 0.1, 0.8, -1.5, 200_000, 200_000 + 86_400, "STOP"),
        _Trade(3.0, 3.1, 0.1, 4.0, -0.5, 300_000, 300_000 + 86_400, "TARGET_2"),
        _Trade(-1.0, -0.9, 0.1, 0.5, -2.0, 400_000, 400_000 + 86_400, "STOP"),
    ]
    metrics = compute_fold_metrics("test", trades)
    assert metrics.n_trades == 5
    assert metrics.hit_rate == pytest.approx(3 / 5)
    assert metrics.expectancy_pct == pytest.approx((2.0 + 1.0 - 0.5 + 3.0 - 1.0) / 5.0)
    assert metrics.max_drawdown_pct <= 0


def test_run_backtest_end_to_end_on_uptrend_series():
    # 150 daily bars, upward drift + realistic volatility
    closes = [100.0 * math.exp(0.003 * i) for i in range(150)]
    daily = _daily(closes)
    # Hourly frame that mirrors daily prices, extended to 200 days worth of hours
    hourly = []
    for i in range(len(daily) - 1):
        for h in range(24):
            price = daily[i].close * (1 + 0.001 * h)  # gentle intraday drift
            open_time = daily[i].close_time + 1 + h * 3600
            close_time = open_time + 3599
            hourly.append(
                Candle(
                    open_time=open_time,
                    close_time=close_time,
                    available_at=close_time,
                    open=price,
                    high=price * 1.005,
                    low=price * 0.995,
                    close=price,
                    volume=1.0,
                    quote_volume=price,
                )
            )
    report = run_backtest(
        symbol="BTCUSDT",
        daily=daily,
        hourly=hourly,
        funding=None,
        params=TsmomParams(),
        n_folds=3,
        embargo_days=2,
        horizon_hours=96,
    )
    assert report.trades > 0
    assert report.aggregate is not None
    assert report.aggregate.n_trades == report.trades
    assert report.benchmark is not None
    assert report.benchmark.n_daily_bars == len(daily)
    assert report.go_no_go is not None
    # verdict fields are populated regardless of pass/fail
    payload = report.to_dict()
    assert payload["benchmark"] is not None
    assert payload["go_no_go"]["overall_go"] in (True, False)
    assert payload["can_authorize_trade"] is False
