"""Buy-and-hold benchmark for the same instrument the strategy trades.

The GO/NO-GO threshold in ``docs/BACKTEST_REPORT_v1.md`` requires the
strategy's cost-adjusted Sharpe to beat the buy-and-hold Sharpe over the
same period, and its max drawdown to stay below 60% of the buy-and-hold
max drawdown. This module computes those two B&H metrics deterministically
from the same daily candle series the strategy consumes.

Fee/slippage/funding are applied to B&H too: one entry, one exit,
accumulated funding across the whole holding period. That way the "beat
B&H" comparison is apples-to-apples — both curves feel the same cost
model, so any Sharpe delta is a pure signal delta.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Sequence

from trading.backtest.cost_model import (
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_TAKER_FEE_BPS,
    CostBreakdown,
    round_trip_cost_pct,
)
from trading.data.binance_perp import Candle, FundingRow


TRADING_DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class BuyHoldMetrics:
    n_daily_bars: int
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    gross_return_pct: float
    cost: CostBreakdown
    net_return_pct: float
    daily_return_stdev_pct: float
    sharpe_annualized: float
    max_drawdown_pct: float
    holding_days: float

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["can_authorize_trade"] = False
        return payload


def _daily_log_returns_pct(closes: Sequence[float]) -> list[float]:
    return [
        (math.log(closes[i] / closes[i - 1])) * 100.0
        for i in range(1, len(closes))
    ]


def _stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _max_drawdown_pct(cumulative_pct: Sequence[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in cumulative_pct:
        peak = max(peak, value)
        worst = min(worst, value - peak)
    return worst


def compute_buy_and_hold(
    *,
    daily: Sequence[Candle],
    funding: Sequence[FundingRow] | None = None,
    taker_fee_bps: float = DEFAULT_TAKER_FEE_BPS,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
) -> BuyHoldMetrics:
    """Return B&H metrics over the same daily series the strategy uses.

    ``daily[0].close`` is the entry, ``daily[-1].close`` is the exit,
    ``entry_time = daily[0].close_time``, ``exit_time = daily[-1].close_time``.
    Funding accrues across the whole holding period (long pays positive
    funding rates). Sharpe is annualised from daily log-returns using
    sqrt(365) since crypto trades every day.

    Fees and slippage are applied once (single round trip).
    """

    if len(daily) < 2:
        raise ValueError("need at least two daily bars for B&H metrics")
    closes = [candle.close for candle in daily]
    entry = closes[0]
    exit_price = closes[-1]
    entry_time = daily[0].close_time
    exit_time = daily[-1].close_time
    if exit_time <= entry_time:
        raise ValueError("daily bars must be strictly chronological")
    gross_pct = (exit_price / entry - 1.0) * 100.0
    cost = round_trip_cost_pct(
        entry_time=entry_time,
        exit_time=exit_time,
        funding_history=funding,
        taker_fee_bps=taker_fee_bps,
        slippage_bps=slippage_bps,
    )
    daily_pct = _daily_log_returns_pct(closes)
    stdev = _stdev(daily_pct)
    mean = sum(daily_pct) / len(daily_pct) if daily_pct else 0.0
    sharpe = (mean / stdev) * math.sqrt(TRADING_DAYS_PER_YEAR) if stdev > 0 else 0.0
    cumulative = []
    running = 0.0
    for r in daily_pct:
        running += r
        cumulative.append(running)
    max_dd = _max_drawdown_pct(cumulative)
    holding_days = (exit_time - entry_time) / 86400.0
    return BuyHoldMetrics(
        n_daily_bars=len(daily),
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=entry,
        exit_price=exit_price,
        gross_return_pct=gross_pct,
        cost=cost,
        net_return_pct=gross_pct - cost.total_pct,
        daily_return_stdev_pct=stdev,
        sharpe_annualized=sharpe,
        max_drawdown_pct=max_dd,
        holding_days=holding_days,
    )


@dataclass(frozen=True)
class GoNoGoVerdict:
    """The three GO/NO-GO checks from docs/BACKTEST_REPORT_v1.md."""

    strategy_sharpe: float
    strategy_max_dd_pct: float
    benchmark_sharpe: float
    benchmark_max_dd_pct: float
    sharpe_above_0_8: bool
    beats_benchmark_sharpe: bool
    drawdown_below_60_pct_of_benchmark: bool
    consistency_folds_above_0_5: int
    total_folds: int
    consistency_ok: bool

    @property
    def overall_go(self) -> bool:
        return (
            self.sharpe_above_0_8
            and self.beats_benchmark_sharpe
            and self.drawdown_below_60_pct_of_benchmark
            and self.consistency_ok
        )

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["overall_go"] = self.overall_go
        return payload


def evaluate_go_no_go(
    *,
    strategy_sharpe: float,
    strategy_max_dd_pct: float,
    benchmark: BuyHoldMetrics,
    fold_sharpes: Sequence[float],
    sharpe_threshold: float = 0.8,
    drawdown_fraction_cap: float = 0.6,
    consistency_min_folds_above: float = 0.5,
    consistency_ratio_required: float = 3 / 4,
) -> GoNoGoVerdict:
    """Compute the four-check verdict.

    Strategy max DD is a negative number (a -20% drawdown). The benchmark
    max DD is also negative. The cap is expressed against absolute values:
    ``abs(strategy_dd) < drawdown_fraction_cap * abs(benchmark_dd)``.
    """

    strategy_dd_abs = abs(strategy_max_dd_pct)
    benchmark_dd_abs = abs(benchmark.max_drawdown_pct)
    dd_ok = (
        benchmark_dd_abs > 0
        and strategy_dd_abs < drawdown_fraction_cap * benchmark_dd_abs
    )
    consistent = sum(1 for s in fold_sharpes if s > consistency_min_folds_above)
    total = len(fold_sharpes)
    consistency_ok = total > 0 and consistent / total >= consistency_ratio_required
    return GoNoGoVerdict(
        strategy_sharpe=strategy_sharpe,
        strategy_max_dd_pct=strategy_max_dd_pct,
        benchmark_sharpe=benchmark.sharpe_annualized,
        benchmark_max_dd_pct=benchmark.max_drawdown_pct,
        sharpe_above_0_8=strategy_sharpe > sharpe_threshold,
        beats_benchmark_sharpe=strategy_sharpe > benchmark.sharpe_annualized,
        drawdown_below_60_pct_of_benchmark=dd_ok,
        consistency_folds_above_0_5=consistent,
        total_folds=total,
        consistency_ok=consistency_ok,
    )
