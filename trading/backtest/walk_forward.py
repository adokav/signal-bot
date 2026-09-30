"""Purged + embargoed walk-forward backtest for the TSMOM long strategy.

The harness reads like the AGENTS.md contract:

- **No look-ahead.** Signals fire on the close of day D using candles up to
  and including D; execution and PnL measurement start after D.
- **Purged folds.** An ``embargo_days`` gap separates each fold's training
  slab from its test slab. TSMOM has no fitted parameters, so the folds are
  out-of-sample sub-periods for consistency checks.
- **Costs scale with exposure.** Fees, slippage and funding are charged on
  the notional actually held (``scale`` × unit notional). Before this fix
  the harness charged full-notional costs on scaled-down positions.
- **Honest horizon.** The exit window must cover the plan's time-stop;
  otherwise trades would be force-closed by the data window and reported
  as ``SERIES_END`` (the v2 run used 96h against a 168h time-stop).
- **Honest annualization.** Sharpe uses the observed trade frequency over
  the calendar span, not ``365 / average_hold``.
- **Evidence, not permission.** The report adds cost stress, entry-delay
  stress, parameter perturbation, bootstrap and Monte Carlo drawdown,
  deflated Sharpe, signal decay and a promotion checklist. Nothing here
  sizes currency positions or authorizes an order (AGENTS.md §4, §10).
"""

from __future__ import annotations

import json
import math
import time
from bisect import bisect_right
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from trading.backtest.benchmark import (
    BuyHoldMetrics,
    GoNoGoVerdict,
    compute_buy_and_hold,
    evaluate_go_no_go,
)
from trading.backtest.cost_model import (
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_TAKER_FEE_BPS,
    CostBreakdown,
    round_trip_cost_pct,
)
from trading.data.binance_perp import Candle, FundingRow
from trading.research.decay import DecayProfile, signal_decay
from trading.research.metrics import (
    PerformanceReport,
    grouped_performance,
    mean,
    performance_report,
    sample_stdev,
)
from trading.research.promotion import (
    FAIL,
    NOT_APPLICABLE,
    NOT_RUN,
    PASS,
    PromotionCheck,
    PromotionReadiness,
    assess_promotion,
)
from trading.research.regime import trend_vol_regime
from trading.research.robustness import (
    CostStressReport,
    DeflatedSharpe,
    MonteCarloDrawdown,
    StabilityVerdict,
    bootstrap_mean_ci,
    deflated_sharpe,
    monte_carlo_drawdown,
    run_cost_stress,
    run_perturbation,
)
from trading.strategies.tsmom import (
    ExitPlan,
    TsmomParams,
    TsmomSignal,
    evaluate_tsmom,
)


TRADING_DAYS_PER_YEAR = 365
ENTRY_DELAY_STRESS_HOURS = (1, 4)
DECAY_HORIZONS_HOURS = (1, 4, 12, 24, 72, 168)
PERTURBATION_KEYS = (
    "lookback_days",
    "realized_vol_lookback_days",
    "target_annualized_vol_pct",
    "atr_lookback_days",
    "stop_atr_mult",
    "target_1_atr_mult",
    "target_2_atr_mult",
)
DSR_PASS_PROBABILITY = 0.95


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SimulatedTrade:
    symbol: str
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    plan: ExitPlan
    scale: float
    gross_return_pct: float  # price return × scale
    cost: CostBreakdown  # per unit notional
    net_return_pct: float  # (price return − unit cost) × scale
    mfe_pct: float
    mae_pct: float
    exit_reason: str
    regime: str = "UNKNOWN"

    @property
    def scaled_cost_pct(self) -> float:
        return self.cost.total_pct * self.scale


def _walk_exit(
    hourly_after_entry: Sequence[Candle],
    plan: ExitPlan,
    *,
    entry_time: int,
) -> tuple[Candle, float, str, float, float]:
    """Walk hourly candles after entry, apply the plan's ladder.

    Rules (long):
    - If any bar's low ``<= plan.hard_stop`` before target_1 fires, the
      trade exits at ``hard_stop``. When one bar touches both stop and a
      target the stop is assumed first (conservative).
    - If target_1 fires, take 50% off and move stop to entry.
    - Under residual, a low ``<= entry`` exits the residual at breakeven;
      target_2 exits it at target_2.
    - Otherwise the time-stop closes the trade at the first bar whose
      close_time is ``>= entry_time + plan.time_stop_seconds``.

    Returns the exit candle, the effective exit price (weighted across
    partial fills), the exit reason, MFE and MAE in percentage points.
    """

    residual_active = False
    stop_price = plan.hard_stop
    filled_price_t1: float | None = None
    mfe = 0.0
    mae = 0.0
    for candle in hourly_after_entry:
        gain_pct = (candle.high / plan.entry_price - 1.0) * 100.0
        loss_pct = (candle.low / plan.entry_price - 1.0) * 100.0
        if gain_pct > mfe:
            mfe = gain_pct
        if loss_pct < mae:
            mae = loss_pct
        if not residual_active:
            if candle.low <= stop_price:
                return candle, stop_price, "STOP", mfe, mae
            if candle.high >= plan.target_1:
                filled_price_t1 = plan.target_1
                residual_active = True
                stop_price = plan.entry_price
                continue
        else:
            if candle.low <= stop_price:
                assert filled_price_t1 is not None
                effective = (filled_price_t1 + stop_price) / 2.0
                return candle, effective, "RESIDUAL_STOP_BREAKEVEN", mfe, mae
            if candle.high >= plan.target_2:
                assert filled_price_t1 is not None
                effective = (filled_price_t1 + plan.target_2) / 2.0
                return candle, effective, "TARGET_2", mfe, mae
        if candle.close_time - entry_time >= plan.time_stop_seconds:
            if filled_price_t1 is not None:
                effective = (filled_price_t1 + candle.close) / 2.0
                return candle, effective, "TIME_STOP_RESIDUAL", mfe, mae
            return candle, candle.close, "TIME_STOP", mfe, mae
    if hourly_after_entry:
        last = hourly_after_entry[-1]
        if filled_price_t1 is not None:
            effective = (filled_price_t1 + last.close) / 2.0
            return last, effective, "SERIES_END_RESIDUAL", mfe, mae
        return last, last.close, "SERIES_END", mfe, mae
    raise ValueError("no post-entry candles to walk")


def simulate_trade(
    *,
    symbol: str,
    plan: ExitPlan,
    scale: float,
    entry_time: int,
    hourly_after_entry: Sequence[Candle],
    funding_history: Sequence[FundingRow] | None,
    taker_fee_bps: float,
    slippage_bps: float,
    regime: str = "UNKNOWN",
) -> SimulatedTrade:
    if scale <= 0 or not math.isfinite(scale):
        raise ValueError("scale must be positive and finite")
    exit_candle, exit_price, reason, mfe, mae = _walk_exit(
        hourly_after_entry, plan, entry_time=entry_time
    )
    price_return_pct = (exit_price / plan.entry_price - 1.0) * 100.0
    cost = round_trip_cost_pct(
        entry_time=entry_time,
        exit_time=exit_candle.close_time,
        funding_history=funding_history,
        taker_fee_bps=taker_fee_bps,
        slippage_bps=slippage_bps,
    )
    return SimulatedTrade(
        symbol=symbol,
        entry_time=entry_time,
        exit_time=exit_candle.close_time,
        entry_price=plan.entry_price,
        exit_price=exit_price,
        plan=plan,
        scale=scale,
        gross_return_pct=price_return_pct * scale,
        cost=cost,
        net_return_pct=(price_return_pct - cost.total_pct) * scale,
        mfe_pct=mfe * scale,
        mae_pct=mae * scale,
        exit_reason=reason,
        regime=regime,
    )


class _HourlySeries:
    """Bisect-indexed hourly candles; avoids a full scan per trade."""

    def __init__(self, hourly: Sequence[Candle]) -> None:
        self.candles = list(hourly)
        self.close_times = [c.close_time for c in self.candles]

    def window(self, start: int, end: int) -> list[Candle]:
        """Candles with ``start < close_time <= end``."""

        lo = bisect_right(self.close_times, start)
        hi = bisect_right(self.close_times, end)
        return self.candles[lo:hi]


# ---------------------------------------------------------------------------
# Fold enumeration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fold:
    name: str
    train_start_idx: int
    train_end_idx: int  # exclusive
    test_start_idx: int
    test_end_idx: int  # exclusive


def enumerate_folds(
    *,
    total_days: int,
    n_folds: int = 4,
    embargo_days: int,
    min_train_days: int,
) -> list[Fold]:
    """Chronological walk-forward folds with an embargo between train and test."""

    if total_days <= 0 or n_folds < 1 or embargo_days < 0:
        raise ValueError("invalid fold arguments")
    usable = total_days - embargo_days
    if usable < min_train_days + n_folds:
        return []
    test_len = max(1, (usable - min_train_days) // n_folds)
    folds: list[Fold] = []
    for k in range(n_folds):
        test_start = min_train_days + k * test_len + embargo_days
        test_end = test_start + test_len
        if test_end > total_days:
            break
        folds.append(
            Fold(
                name=f"fold_{k + 1}",
                train_start_idx=0,
                train_end_idx=test_start - embargo_days,
                test_start_idx=test_start,
                test_end_idx=test_end,
            )
        )
    return folds


# ---------------------------------------------------------------------------
# Fold metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldMetrics:
    fold: str
    n_trades: int
    hit_rate: float
    expectancy_pct: float
    mean_gross_pct: float
    mean_cost_pct: float
    mean_net_pct: float
    stdev_net_pct: float
    sharpe_annualized: float
    max_drawdown_pct: float
    mean_mfe_pct: float
    mean_mae_pct: float
    exit_reason_breakdown: dict[str, int]
    span_days: float = 0.0


def _max_drawdown_pct(returns: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for r in returns:
        equity += r
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _span_days_of(trades: Sequence[SimulatedTrade]) -> float:
    if not trades:
        return 1.0
    start = min(t.entry_time for t in trades)
    end = max(t.exit_time for t in trades)
    return max(1.0, (end - start) / 86400.0)


def compute_fold_metrics(
    fold_name: str,
    trades: Sequence[SimulatedTrade],
    *,
    span_days: float | None = None,
) -> FoldMetrics:
    """Per-fold metrics. Sharpe is annualized by ``n_trades / span_years``.

    ``span_days`` should be the calendar length of the window the trades
    were drawn from; when omitted it falls back to first entry → last exit.
    """

    span = span_days if span_days is not None else _span_days_of(trades)
    if span <= 0:
        raise ValueError("span_days must be positive")
    if not trades:
        return FoldMetrics(
            fold=fold_name, n_trades=0, hit_rate=0.0, expectancy_pct=0.0,
            mean_gross_pct=0.0, mean_cost_pct=0.0, mean_net_pct=0.0,
            stdev_net_pct=0.0, sharpe_annualized=0.0, max_drawdown_pct=0.0,
            mean_mfe_pct=0.0, mean_mae_pct=0.0, exit_reason_breakdown={},
            span_days=span,
        )
    net = [trade.net_return_pct for trade in trades]
    gross = [trade.gross_return_pct for trade in trades]
    costs = [trade.scaled_cost_pct for trade in trades]
    wins = sum(1 for r in net if r > 0)
    stdev_net = sample_stdev(net)
    mean_net = mean(net)
    trades_per_year = len(trades) / (span / TRADING_DAYS_PER_YEAR)
    sharpe = (mean_net / stdev_net) * math.sqrt(trades_per_year) if stdev_net > 0 else 0.0
    breakdown: dict[str, int] = {}
    for trade in trades:
        breakdown[trade.exit_reason] = breakdown.get(trade.exit_reason, 0) + 1
    return FoldMetrics(
        fold=fold_name,
        n_trades=len(trades),
        hit_rate=wins / len(trades),
        expectancy_pct=mean_net,
        mean_gross_pct=mean(gross),
        mean_cost_pct=mean(costs),
        mean_net_pct=mean_net,
        stdev_net_pct=stdev_net,
        sharpe_annualized=sharpe,
        max_drawdown_pct=_max_drawdown_pct(net),
        mean_mfe_pct=mean([t.mfe_pct for t in trades]),
        mean_mae_pct=mean([t.mae_pct for t in trades]),
        exit_reason_breakdown=breakdown,
        span_days=span,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class BacktestReport:
    symbol: str
    params: TsmomParams
    folds: list[FoldMetrics] = field(default_factory=list)
    aggregate: FoldMetrics | None = None
    benchmark: BuyHoldMetrics | None = None
    go_no_go: GoNoGoVerdict | None = None
    total_days: int = 0
    daily_bars: int = 0
    hourly_bars: int = 0
    trades: int = 0
    embargo_days: int = 0
    horizon_hours: int = 0
    single_position: bool = True
    performance: PerformanceReport | None = None
    performance_by_regime: dict[str, PerformanceReport] = field(default_factory=dict)
    performance_by_year: dict[str, PerformanceReport] = field(default_factory=dict)
    cost_stress: CostStressReport | None = None
    entry_delay_stress: list[dict] = field(default_factory=list)
    entry_delay_verdict: str = "NOT_RUN"
    bootstrap_expectancy_ci: tuple[float, float] | None = None
    monte_carlo: MonteCarloDrawdown | None = None
    deflated: DeflatedSharpe | None = None
    parameter_stability: StabilityVerdict | None = None
    decay: DecayProfile | None = None
    promotion: PromotionReadiness | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "params": asdict(self.params),
            "total_days": self.total_days,
            "daily_bars": self.daily_bars,
            "hourly_bars": self.hourly_bars,
            "trades": self.trades,
            "embargo_days": self.embargo_days,
            "horizon_hours": self.horizon_hours,
            "single_position": self.single_position,
            "benchmark": self.benchmark.to_dict() if self.benchmark else None,
            "go_no_go": self.go_no_go.to_dict() if self.go_no_go else None,
            "folds": [asdict(fold) for fold in self.folds],
            "aggregate": asdict(self.aggregate) if self.aggregate else None,
            "performance": self.performance.to_dict() if self.performance else None,
            "performance_by_regime": {k: v.to_dict() for k, v in self.performance_by_regime.items()},
            "performance_by_year": {k: v.to_dict() for k, v in self.performance_by_year.items()},
            "cost_stress": self.cost_stress.to_dict() if self.cost_stress else None,
            "entry_delay_stress": list(self.entry_delay_stress),
            "entry_delay_verdict": self.entry_delay_verdict,
            "bootstrap_expectancy_ci": (
                list(self.bootstrap_expectancy_ci) if self.bootstrap_expectancy_ci else None
            ),
            "monte_carlo_drawdown": self.monte_carlo.to_dict() if self.monte_carlo else None,
            "deflated_sharpe": self.deflated.to_dict() if self.deflated else None,
            "parameter_stability": (
                self.parameter_stability.to_dict() if self.parameter_stability else None
            ),
            "signal_decay": self.decay.to_dict() if self.decay else None,
            "promotion": self.promotion.to_dict() if self.promotion else None,
            "notes": list(self.notes),
            "can_authorize_trade": False,
        }


# ---------------------------------------------------------------------------
# Simulation core
# ---------------------------------------------------------------------------


def _min_start(params: TsmomParams) -> int:
    return max(params.lookback_days, params.realized_vol_lookback_days, params.atr_lookback_days)


@dataclass(frozen=True)
class _SimulationResult:
    trades: list[SimulatedTrade]
    long_signal_times: list[int]
    skipped_by_delay: int


def _simulate(
    *,
    symbol: str,
    daily: Sequence[Candle],
    hourly: _HourlySeries,
    funding: Sequence[FundingRow] | None,
    params: TsmomParams,
    taker_fee_bps: float,
    slippage_bps: float,
    horizon_hours: int,
    single_position: bool,
    entry_delay_hours: int = 0,
    label_regimes: bool = False,
) -> _SimulationResult:
    if entry_delay_hours < 0:
        raise ValueError("entry delay cannot be negative")
    trades: list[SimulatedTrade] = []
    long_signal_times: list[int] = []
    skipped = 0
    closes = [c.close for c in daily]
    active_exit_time: int | None = None
    for i in range(_min_start(params), len(daily) - 1):
        decision_time = daily[i].close_time
        decision = evaluate_tsmom(
            daily[: i + 1], symbol=symbol, decision_at=decision_time, params=params
        )
        if decision.signal is not TsmomSignal.LONG or decision.plan is None:
            continue
        long_signal_times.append(decision_time)
        if single_position and active_exit_time is not None and decision_time < active_exit_time:
            continue
        plan = decision.plan
        entry_time = decision_time
        if entry_delay_hours:
            delayed = hourly.window(decision_time, decision_time + entry_delay_hours * 3600)
            if not delayed:
                skipped += 1
                continue
            entry_bar = delayed[-1]
            try:
                plan = ExitPlan(
                    entry_price=entry_bar.close,
                    hard_stop=plan.hard_stop,
                    target_1=plan.target_1,
                    target_2=plan.target_2,
                    time_stop_seconds=plan.time_stop_seconds,
                )
            except ValueError:
                # Price already left the planned band: a delayed trader
                # would not take this trade at the original levels.
                skipped += 1
                continue
            entry_time = entry_bar.close_time
        post = hourly.window(entry_time, entry_time + horizon_hours * 3600)
        if not post:
            continue
        regime = trend_vol_regime(closes[: i + 1]) if label_regimes else "UNKNOWN"
        trade = simulate_trade(
            symbol=symbol,
            plan=plan,
            scale=decision.position_scale,
            entry_time=entry_time,
            hourly_after_entry=post,
            funding_history=funding,
            taker_fee_bps=taker_fee_bps,
            slippage_bps=slippage_bps,
            regime=regime,
        )
        trades.append(trade)
        active_exit_time = trade.exit_time
    return _SimulationResult(trades, long_signal_times, skipped)


def _year_of(trade: SimulatedTrade) -> str:
    return str(datetime.fromtimestamp(trade.entry_time, tz=timezone.utc).year)


def _promotion_checks(
    *,
    go_no_go: GoNoGoVerdict,
    cost_stress: CostStressReport,
    entry_delay_verdict: str,
    stability: StabilityVerdict | None,
    bootstrap_ci: tuple[float, float] | None,
    monte_carlo: MonteCarloDrawdown | None,
    benchmark: BuyHoldMetrics,
    dsr: DeflatedSharpe | None,
) -> dict[str, PromotionCheck]:
    def check(name: str, state: str, detail: str) -> PromotionCheck:
        return PromotionCheck(name=name, state=state, detail=detail)

    oos_ok = go_no_go.sharpe_above_0_8 and go_no_go.beats_benchmark_sharpe and go_no_go.drawdown_below_60_pct_of_benchmark
    checks = {
        "out_of_sample": check(
            "out_of_sample", PASS if oos_ok else FAIL,
            f"sharpe={go_no_go.strategy_sharpe:.2f} vs B&H {go_no_go.benchmark_sharpe:.2f}",
        ),
        "walk_forward": check(
            "walk_forward", PASS if go_no_go.consistency_ok else FAIL,
            f"{go_no_go.consistency_folds_above_0_5}/{go_no_go.total_folds} fold Sharpe > 0.5",
        ),
        "transaction_cost_stress": check(
            "transaction_cost_stress",
            PASS if cost_stress.verdict == "COST_ROBUST" else FAIL,
            cost_stress.verdict,
        ),
        "entry_delay_stress": check(
            "entry_delay_stress",
            PASS if entry_delay_verdict == "LATENCY_ROBUST" else FAIL,
            entry_delay_verdict,
        ),
    }
    if stability is None or stability.verdict == "NOT_RUN":
        checks["parameter_perturbation"] = check("parameter_perturbation", NOT_RUN, "--perturb ile çalıştırılmadı")
    else:
        checks["parameter_perturbation"] = check(
            "parameter_perturbation", PASS if stability.verdict == "ROBUST" else FAIL, stability.verdict
        )
    if bootstrap_ci is None:
        checks["bootstrap_expectancy"] = check("bootstrap_expectancy", FAIL, "yetersiz işlem")
    else:
        checks["bootstrap_expectancy"] = check(
            "bootstrap_expectancy", PASS if bootstrap_ci[0] > 0 else FAIL,
            f"95% CI [{bootstrap_ci[0]:.3f}, {bootstrap_ci[1]:.3f}] %/işlem",
        )
    if monte_carlo is None or benchmark.max_drawdown_pct >= 0:
        checks["monte_carlo_drawdown"] = check("monte_carlo_drawdown", FAIL, "hesaplanamadı")
    else:
        limit = 0.6 * abs(benchmark.max_drawdown_pct)
        checks["monte_carlo_drawdown"] = check(
            "monte_carlo_drawdown",
            PASS if abs(monte_carlo.p05_max_dd_pct) < limit else FAIL,
            f"p05 DD {monte_carlo.p05_max_dd_pct:.1f}% vs limit -{limit:.1f}%",
        )
    if dsr is None or dsr.deflated_sharpe_probability is None:
        checks["deflated_sharpe"] = check("deflated_sharpe", FAIL, "hesaplanamadı")
    else:
        checks["deflated_sharpe"] = check(
            "deflated_sharpe",
            PASS if dsr.deflated_sharpe_probability >= DSR_PASS_PROBABILITY else FAIL,
            f"DSR={dsr.deflated_sharpe_probability:.3f}, N={dsr.n_trials}",
        )
    checks["probability_calibration"] = check(
        "probability_calibration", NOT_APPLICABLE,
        "strateji olasılık üretmiyor; EV ampirik isabet oranından hesaplanır",
    )
    return checks


def run_backtest(
    *,
    symbol: str,
    daily: Sequence[Candle],
    hourly: Sequence[Candle],
    funding: Sequence[FundingRow] | None = None,
    params: TsmomParams = TsmomParams(),
    n_folds: int = 4,
    embargo_days: int = 3,
    taker_fee_bps: float = DEFAULT_TAKER_FEE_BPS,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
    horizon_hours: int = 168,
    single_position: bool = True,
    perturb: bool = False,
    n_trials: int = 1,
    other_trial_sharpes: Sequence[float] = (),
    seed: int = 0,
) -> BacktestReport:
    """Run the purged walk-forward backtest and the §17/§18/§19/§41 evidence set.

    ``single_position`` (default True) prevents a new trade while one is
    open; ``False`` restores event-independent simulation for diagnostics.
    ``n_trials`` is the number of distinct strategy configurations tried in
    this family, including this one (from the trial registry).
    """

    if len(daily) < params.lookback_days + params.realized_vol_lookback_days + 2:
        raise ValueError("not enough daily bars for the configured lookbacks")
    if not hourly:
        raise ValueError("hourly bars required for exit simulation")
    if horizon_hours * 3600 < params.time_stop_seconds:
        raise ValueError(
            f"horizon_hours={horizon_hours} is shorter than the plan time-stop "
            f"({params.time_stop_seconds // 3600}h); trades would be cut by the data window"
        )
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    hourly_series = _HourlySeries(hourly)
    common = dict(
        symbol=symbol, daily=daily, hourly=hourly_series, funding=funding,
        taker_fee_bps=taker_fee_bps, slippage_bps=slippage_bps,
        horizon_hours=horizon_hours, single_position=single_position,
    )
    base = _simulate(params=params, label_regimes=True, **common)
    trades = base.trades
    net = [t.net_return_pct for t in trades]

    total_days = len(daily)
    min_start = _min_start(params)
    span_days = max(1.0, (daily[-1].close_time - daily[min_start].close_time) / 86400.0)
    folds = enumerate_folds(
        total_days=total_days, n_folds=n_folds, embargo_days=embargo_days, min_train_days=min_start,
    )
    fold_metrics: list[FoldMetrics] = []
    for fold in folds:
        window_start = daily[fold.test_start_idx].close_time
        window_end = daily[min(fold.test_end_idx, total_days) - 1].close_time
        fold_trades = [t for t in trades if window_start <= t.entry_time < window_end]
        fold_span = max(1.0, (window_end - window_start) / 86400.0)
        fold_metrics.append(compute_fold_metrics(fold.name, fold_trades, span_days=fold_span))

    aggregate = compute_fold_metrics("aggregate_all_folds", trades, span_days=span_days)
    benchmark = compute_buy_and_hold(
        daily=daily, funding=funding, taker_fee_bps=taker_fee_bps, slippage_bps=slippage_bps,
    )
    go_no_go = evaluate_go_no_go(
        strategy_sharpe=aggregate.sharpe_annualized,
        strategy_max_dd_pct=aggregate.max_drawdown_pct,
        benchmark=benchmark,
        fold_sharpes=[fold.sharpe_annualized for fold in fold_metrics],
    )

    performance = performance_report(net, span_days=span_days)
    by_regime = grouped_performance(trades, key=lambda t: t.regime, span_days=span_days)
    by_year = grouped_performance(trades, key=_year_of, span_days=span_days)
    cost_stress = run_cost_stress(trades)

    delay_rows = []
    base_expectancy = mean(net)
    for delay in ENTRY_DELAY_STRESS_HOURS:
        delayed = _simulate(params=params, entry_delay_hours=delay, **common)
        delayed_net = [t.net_return_pct for t in delayed.trades]
        delay_rows.append({
            "delay_hours": delay,
            "n_trades": len(delayed.trades),
            "skipped_price_left_plan": delayed.skipped_by_delay,
            "expectancy_pct": mean(delayed_net),
        })
    if not trades or base_expectancy <= 0:
        delay_verdict = "NO_EDGE"
    elif any(row["expectancy_pct"] <= 0 for row in delay_rows):
        delay_verdict = "LATENCY_FRAGILE"
    else:
        delay_verdict = "LATENCY_ROBUST"

    stability = None
    if perturb:
        base_dict = asdict(params)

        def _evaluate(candidate: dict) -> tuple[float | None, int]:
            try:
                candidate_params = TsmomParams(**candidate)
                result = _simulate(params=candidate_params, **common)
            except ValueError:
                return None, 0
            values = [t.net_return_pct for t in result.trades]
            return mean(values), len(values)

        stability = run_perturbation(
            base_dict, keys=PERTURBATION_KEYS, evaluate=_evaluate,
            metric_name="expectancy_pct", base_metric=base_expectancy,
        )

    bootstrap_ci = bootstrap_mean_ci(net, seed=seed) if len(net) >= 2 else None
    monte_carlo = monte_carlo_drawdown(
        net, seed=seed, threshold_pct=0.6 * abs(benchmark.max_drawdown_pct) or None,
    )
    dsr = deflated_sharpe(net, n_trials=n_trials, trial_sharpes=[
        *other_trial_sharpes,
        *( [mean(net) / sample_stdev(net)] if len(net) >= 2 and sample_stdev(net) > 0 else [] ),
    ])
    decay = None
    if base.long_signal_times:
        decay = signal_decay(
            base.long_signal_times, hourly_series.candles,
            horizons_seconds=[h * 3600 for h in DECAY_HORIZONS_HOURS],
        )
    promotion = assess_promotion(_promotion_checks(
        go_no_go=go_no_go, cost_stress=cost_stress, entry_delay_verdict=delay_verdict,
        stability=stability, bootstrap_ci=bootstrap_ci, monte_carlo=monte_carlo,
        benchmark=benchmark, dsr=dsr,
    ))
    notes = [
        "Özsermaye eğrisi toplamsal (sabit notional, bileşik değil).",
        "Giriş gecikmesi stresi 1 saatlik mum çözünürlüğündedir; saniye düzeyi gecikme bu veriyle ölçülemez.",
        "Spread ayrı modellenmiyor; slippage figürü yarım spread + etkiyi temsil eder.",
        "Rejim etiketleri yalnızca betimleyicidir (trend × vol); işlem izni değildir.",
    ]
    if funding is None:
        notes.append("Funding verisi yok: maliyetler olduğundan düşük gösterilir.")
    return BacktestReport(
        symbol=symbol.upper(),
        params=params,
        folds=fold_metrics,
        aggregate=aggregate,
        benchmark=benchmark,
        go_no_go=go_no_go,
        total_days=total_days,
        daily_bars=len(daily),
        hourly_bars=len(hourly),
        trades=len(trades),
        embargo_days=embargo_days,
        horizon_hours=horizon_hours,
        single_position=single_position,
        performance=performance,
        performance_by_regime=by_regime,
        performance_by_year=by_year,
        cost_stress=cost_stress,
        entry_delay_stress=delay_rows,
        entry_delay_verdict=delay_verdict,
        bootstrap_expectancy_ci=bootstrap_ci,
        monte_carlo=monte_carlo,
        deflated=dsr,
        parameter_stability=stability,
        decay=decay,
        promotion=promotion,
        notes=notes,
    )


def write_report(report: BacktestReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def evidence_summary(report: BacktestReport) -> dict:
    """Compact evidence block for the strategy markdown report."""

    perf = report.performance
    return {
        "symbol": report.symbol,
        "trades": report.trades,
        "horizon_hours": report.horizon_hours,
        "performance": perf.to_dict() if perf else None,
        "benchmark_sharpe": report.benchmark.sharpe_annualized if report.benchmark else None,
        "benchmark_max_dd_pct": report.benchmark.max_drawdown_pct if report.benchmark else None,
        "go_no_go": report.go_no_go.to_dict() if report.go_no_go else None,
        "cost_stress": report.cost_stress.to_dict() if report.cost_stress else None,
        "entry_delay_stress": report.entry_delay_stress,
        "entry_delay_verdict": report.entry_delay_verdict,
        "bootstrap_expectancy_ci": report.bootstrap_expectancy_ci,
        "monte_carlo_drawdown": report.monte_carlo.to_dict() if report.monte_carlo else None,
        "deflated_sharpe": report.deflated.to_dict() if report.deflated else None,
        "parameter_stability": (
            {k: v for k, v in report.parameter_stability.to_dict().items() if k != "neighbors"}
            if report.parameter_stability else None
        ),
        "by_regime": {
            k: {"n": v.n_trades, "expectancy_pct": v.expectancy_pct, "profit_factor": v.profit_factor}
            for k, v in report.performance_by_regime.items()
        },
        "by_year": {
            k: {"n": v.n_trades, "expectancy_pct": v.expectancy_pct, "max_dd_pct": v.max_drawdown_pct}
            for k, v in report.performance_by_year.items()
        },
        "signal_decay": (
            {
                "peak_horizon_hours": (report.decay.peak_horizon_seconds or 0) / 3600 or None,
                "fade_horizon_hours": (report.decay.fade_horizon_seconds or 0) / 3600 or None,
                "horizons": [
                    {
                        "hours": h.horizon_seconds / 3600,
                        "n_independent": h.n_independent,
                        "mean_pct": h.independent_mean_pct,
                        "excess_vs_baseline_pct": h.excess_mean_pct,
                        "t_stat": h.independent_t_stat,
                    }
                    for h in report.decay.horizons
                ],
            }
            if report.decay else None
        ),
        "promotion": report.promotion.to_dict() if report.promotion else None,
        "notes": report.notes,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_candles(path: Path) -> list[Candle]:
    import pandas as pd

    frame = pd.read_parquet(path)
    return [
        Candle(
            open_time=int(row.open_time),
            close_time=int(row.close_time),
            available_at=int(row.available_at),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            quote_volume=float(row.quote_volume),
        )
        for row in frame.itertuples(index=False)
    ]


def _load_funding(path: Path) -> list[FundingRow]:
    import pandas as pd

    frame = pd.read_parquet(path)
    return [
        FundingRow(
            symbol=str(row.symbol),
            funding_time=int(row.funding_time),
            available_at=int(row.available_at),
            funding_rate=float(row.funding_rate),
        )
        for row in frame.itertuples(index=False)
    ]


def _trial_params(params: TsmomParams, single_position: bool) -> dict:
    return {**asdict(params), "single_position": single_position}


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse

    from trading.research.report import StrategyReport
    from trading.research.robustness import TrialRecord, TrialRegistry, trial_id_for
    from trading.strategies.tsmom_dossier import (
        TSMOM_BLOW_UP_SCENARIOS,
        TSMOM_FAMILY,
        TSMOM_FRAGILITY_ANSWERS,
        TSMOM_HYPOTHESIS,
    )

    parser = argparse.ArgumentParser(
        description="Run the TSMOM walk-forward backtest on stored parquet data."
    )
    parser.add_argument("symbol", help="e.g. BTCUSDT")
    parser.add_argument("--data-dir", type=Path, default=Path("research/data/binance_perp"))
    parser.add_argument("--out", type=Path, default=Path("research/data/backtest_report.json"))
    parser.add_argument("--report-md", type=Path, default=None, help="strategy markdown report path")
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--embargo-days", type=int, default=3)
    parser.add_argument(
        "--horizon-hours", type=int, default=168,
        help="max hours per trade for exit simulation; must be >= the plan time-stop",
    )
    parser.add_argument("--allow-overlapping", action="store_true",
                        help="disable single-position mode (allow stacked trades)")
    parser.add_argument("--perturb", action="store_true", help="run parameter perturbation (§15)")
    parser.add_argument("--trial-registry", type=Path, default=Path("research/trials/registry.jsonl"))
    parser.add_argument("--record-trial", action="store_true",
                        help="append this configuration to the trial registry")
    args = parser.parse_args(list(argv) if argv is not None else None)

    symbol = args.symbol.upper()
    daily = _load_candles(args.data_dir / f"{symbol}_klines_1d.parquet")
    hourly = _load_candles(args.data_dir / f"{symbol}_klines_1h.parquet")
    funding_path = args.data_dir / f"{symbol}_funding.parquet"
    funding = _load_funding(funding_path) if funding_path.exists() else None

    params = TsmomParams()
    single_position = not args.allow_overlapping
    trial_params = _trial_params(params, single_position)
    trial_id = trial_id_for(family=TSMOM_FAMILY, params=trial_params, dataset={})
    registry = TrialRegistry(args.trial_registry)
    prior = registry.selection_trials(TSMOM_FAMILY)
    known_ids = {r.trial_id for r in prior}
    n_trials = len(known_ids | {trial_id})
    other_sharpes = [
        r.sharpe_per_trade for r in prior
        if r.trial_id != trial_id and r.sharpe_per_trade is not None
    ]

    report = run_backtest(
        symbol=symbol, daily=daily, hourly=hourly, funding=funding, params=params,
        n_folds=args.folds, embargo_days=args.embargo_days,
        horizon_hours=args.horizon_hours, single_position=single_position,
        perturb=args.perturb, n_trials=n_trials, other_trial_sharpes=other_sharpes,
    )
    write_report(report, args.out)
    md_path = args.report_md or args.out.with_suffix(".md")
    strategy_report = StrategyReport(
        hypothesis=TSMOM_HYPOTHESIS,
        fragility_answers=TSMOM_FRAGILITY_ANSWERS,
        blow_up_scenarios=TSMOM_BLOW_UP_SCENARIOS,
        evidence=evidence_summary(report),
    )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(strategy_report.render_markdown(), encoding="utf-8")

    if args.record_trial:
        registry.append(TrialRecord(
            trial_id=trial_id,
            family=TSMOM_FAMILY,
            kind="SELECTION_CANDIDATE",
            description="walk_forward CLI run",
            params=trial_params,
            dataset={"symbol": symbol, "daily_bars": len(daily), "first_close": daily[0].close_time,
                     "last_close": daily[-1].close_time},
            recorded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            n_trades=report.trades,
            sharpe_per_trade=report.deflated.sharpe_per_trade if report.deflated else None,
        ))

    print(f"wrote {args.out} and {md_path}")
    print(f"trades: {report.trades}  (trials in family: {n_trials})")
    perf = report.performance
    if perf:
        pf = f"{perf.profit_factor:.2f}" if perf.profit_factor is not None else "n/a"
        print(
            f"strategy : EV/trade={perf.expectancy_pct:.3f}%  PF={pf}  "
            f"sharpe={perf.sharpe_annualized:.2f}  maxdd={perf.max_drawdown_pct:.2f}%  "
            f"win={perf.win_rate:.1%}  sample={perf.sample_quality}"
        )
    if report.benchmark:
        print(
            f"benchmark: net={report.benchmark.net_return_pct:.2f}%  "
            f"sharpe={report.benchmark.sharpe_annualized:.2f}  "
            f"maxdd={report.benchmark.max_drawdown_pct:.2f}%"
        )
    if report.cost_stress:
        print(f"cost     : {report.cost_stress.verdict}   latency: {report.entry_delay_verdict}")
    if report.deflated and report.deflated.deflated_sharpe_probability is not None:
        print(f"DSR      : {report.deflated.deflated_sharpe_probability:.3f} (N={report.deflated.n_trials})")
    if report.promotion:
        print(f"stage    : {report.promotion.stage}  (promotable_to_live={report.promotion.promotable_to_live})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
