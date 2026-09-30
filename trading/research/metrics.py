"""Trade-level performance metrics (spec §18).

Win rate is reported but deliberately treated as auxiliary: the headline
numbers are expectancy after costs, profit factor, drawdown and tail loss.

Conventions:

- Inputs are per-trade **net** returns in percentage points of notional,
  already reduced by fees, slippage and funding.
- The equity curve is additive (fixed notional, no compounding). That is
  the honest convention for a strategy that risks a fixed notional per
  trade; compounding would flatter a strategy with a lucky early streak.
- Annualization uses the *observed* trade frequency over the calendar span
  (``n_trades / span_years``), not ``365 / average_hold``. The latter
  assumes the strategy is always in a trade and overstates Sharpe for a
  single-position strategy that sits flat between signals.
- Undefined ratios (profit factor without losses, Calmar without drawdown)
  are ``None`` rather than infinity, so JSON stays valid and a reader
  cannot mistake "no losses in 7 trades" for an infinite edge.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Protocol, Sequence


DAYS_PER_YEAR = 365.0
MIN_TRADES_FOR_INFERENCE = 30
ADEQUATE_TRADES = 100


class TradeLike(Protocol):
    entry_time: int
    exit_time: int
    net_return_pct: float


def _check_finite(values: Sequence[float]) -> None:
    for value in values:
        if not math.isfinite(value):
            raise ValueError("non-finite trade return")


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def sample_stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = mean(values)
    return math.sqrt(sum((v - mu) ** 2 for v in values) / (len(values) - 1))


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile, ``q`` in [0, 1]."""

    if not values:
        raise ValueError("percentile of empty sequence")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be within [0, 1]")
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def max_drawdown_pct(returns: Sequence[float]) -> float:
    """Worst peak-to-trough of the additive equity curve; <= 0."""

    equity = 0.0
    peak = 0.0
    worst = 0.0
    for r in returns:
        equity += r
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def max_consecutive_losses(returns: Sequence[float]) -> int:
    longest = 0
    current = 0
    for r in returns:
        if r <= 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def sample_quality(n_trades: int) -> str:
    if n_trades < MIN_TRADES_FOR_INFERENCE:
        return "INSUFFICIENT"
    if n_trades < ADEQUATE_TRADES:
        return "SMALL"
    return "ADEQUATE"


@dataclass(frozen=True)
class PerformanceReport:
    n_trades: int
    span_days: float
    sample_quality: str
    expectancy_pct: float
    total_net_return_pct: float
    annualized_return_pct: float
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    payoff_ratio: float | None
    profit_factor: float | None
    max_drawdown_pct: float
    return_over_drawdown: float | None
    sharpe_annualized: float
    sortino_annualized: float | None
    calmar_ratio: float | None
    tail_loss_p5_pct: float | None
    expected_shortfall_p5_pct: float | None
    max_consecutive_losses: int
    trades_per_year: float
    stdev_pct: float
    skewness: float | None
    kurtosis: float | None

    def to_dict(self) -> dict:
        return asdict(self)


def moments(values: Sequence[float]) -> tuple[float | None, float | None]:
    """Sample skewness and (non-excess) kurtosis; ``None`` when undefined."""

    n = len(values)
    if n < 4:
        return None, None
    mu = mean(values)
    m2 = sum((v - mu) ** 2 for v in values) / n
    if m2 <= 0:
        return None, None
    m3 = sum((v - mu) ** 3 for v in values) / n
    m4 = sum((v - mu) ** 4 for v in values) / n
    return m3 / m2 ** 1.5, m4 / m2 ** 2


def performance_report(
    returns_pct: Sequence[float],
    *,
    span_days: float,
) -> PerformanceReport:
    """Compute the §18 metric set from chronologically ordered net returns.

    ``span_days`` is the calendar length of the evaluation window (not the
    sum of holding periods). It drives annualization.
    """

    values = list(returns_pct)
    _check_finite(values)
    if span_days <= 0:
        raise ValueError("span_days must be positive")
    n = len(values)
    span_years = span_days / DAYS_PER_YEAR
    if n == 0:
        return PerformanceReport(
            n_trades=0, span_days=span_days, sample_quality=sample_quality(0),
            expectancy_pct=0.0, total_net_return_pct=0.0, annualized_return_pct=0.0,
            win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0, payoff_ratio=None,
            profit_factor=None, max_drawdown_pct=0.0, return_over_drawdown=None,
            sharpe_annualized=0.0, sortino_annualized=None, calmar_ratio=None,
            tail_loss_p5_pct=None, expected_shortfall_p5_pct=None,
            max_consecutive_losses=0, trades_per_year=0.0, stdev_pct=0.0,
            skewness=None, kurtosis=None,
        )
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v <= 0]
    total = sum(values)
    expectancy = total / n
    stdev = sample_stdev(values)
    trades_per_year = n / span_years
    sharpe = (expectancy / stdev) * math.sqrt(trades_per_year) if stdev > 0 else 0.0
    downside = math.sqrt(sum(min(v, 0.0) ** 2 for v in values) / n)
    sortino = (expectancy / downside) * math.sqrt(trades_per_year) if downside > 0 else None
    dd = max_drawdown_pct(values)
    annualized = total / span_years
    gross_loss = -sum(losses)
    avg_win = mean(wins)
    avg_loss = mean(losses)
    skew, kurt = moments(values)
    tail = percentile(values, 0.05) if n >= MIN_TRADES_FOR_INFERENCE else None
    if tail is not None:
        worst = [v for v in values if v <= tail]
        shortfall = mean(worst)
    else:
        shortfall = None
    return PerformanceReport(
        n_trades=n,
        span_days=span_days,
        sample_quality=sample_quality(n),
        expectancy_pct=expectancy,
        total_net_return_pct=total,
        annualized_return_pct=annualized,
        win_rate=len(wins) / n,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        payoff_ratio=(avg_win / abs(avg_loss)) if wins and avg_loss < 0 else None,
        profit_factor=(sum(wins) / gross_loss) if gross_loss > 0 else None,
        max_drawdown_pct=dd,
        return_over_drawdown=(total / abs(dd)) if dd < 0 else None,
        sharpe_annualized=sharpe,
        sortino_annualized=sortino,
        calmar_ratio=(annualized / abs(dd)) if dd < 0 else None,
        tail_loss_p5_pct=tail,
        expected_shortfall_p5_pct=shortfall,
        max_consecutive_losses=max_consecutive_losses(values),
        trades_per_year=trades_per_year,
        stdev_pct=stdev,
        skewness=skew,
        kurtosis=kurt,
    )


def span_days_of(trades: Sequence[TradeLike]) -> float:
    """Calendar span from first entry to last exit, floored at one day."""

    if not trades:
        return 1.0
    start = min(t.entry_time for t in trades)
    end = max(t.exit_time for t in trades)
    return max(1.0, (end - start) / 86400.0)


def grouped_performance(
    trades: Iterable[TradeLike],
    *,
    key: Callable[[TradeLike], str],
    span_days: float,
) -> dict[str, PerformanceReport]:
    """Per-group metrics (by regime, asset, setup type, …) — spec §7, §18.

    Every group is annualized over the same ``span_days`` so groups remain
    comparable: a regime that occupied 10% of the calendar and produced few
    trades must not look like a high-frequency strategy.
    """

    buckets: dict[str, list[TradeLike]] = {}
    for trade in trades:
        buckets.setdefault(str(key(trade)), []).append(trade)
    return {
        name: performance_report(
            [t.net_return_pct for t in sorted(rows, key=lambda t: t.entry_time)],
            span_days=span_days,
        )
        for name, rows in sorted(buckets.items())
    }
