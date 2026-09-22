"""Purged + embargoed walk-forward backtest for the TSMOM long strategy.

The harness is deliberately compact and reads like the AGENTS.md contract:

- **No look-ahead.** Signals fire on the close of day D; execution and PnL
  measurement start at the first bar after D. The strategy's own
  ``evaluate_tsmom`` uses only ``candles[:-1]`` for the vol/return
  computation when the harness feeds it up to the decision index.
- **Purged folds.** When folds are enumerated, the harness leaves an
  ``embargo_days`` gap between the training set's tail and the test set's
  head so overlapping label horizons do not leak across the split.
- **Costs applied.** Every trade's realized return is the gross OHLC
  simulation minus the cost model's round-trip cost, including funding.
- **Metrics.** Cost-adjusted Sharpe (annualized), expectancy, MFE/MAE,
  max drawdown, hit rate and trade count. These are reported per fold and
  aggregated.

The harness does not size positions in currency and never authorizes an
order (AGENTS.md §4, §10). It reports return distributions the reviewer
inspects before Faz C is permitted to start.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from trading.backtest.cost_model import (
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_TAKER_FEE_BPS,
    CostBreakdown,
    round_trip_cost_pct,
)
from trading.data.binance_perp import Candle, FundingRow
from trading.strategies.tsmom import (
    ExitPlan,
    TsmomDecision,
    TsmomParams,
    TsmomSignal,
    evaluate_tsmom,
)


TRADING_DAYS_PER_YEAR = 365


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
    gross_return_pct: float
    cost: CostBreakdown
    net_return_pct: float
    mfe_pct: float
    mae_pct: float
    exit_reason: str


def _walk_exit(
    hourly_after_entry: Sequence[Candle],
    plan: ExitPlan,
    *,
    entry_time: int,
) -> tuple[Candle, float, str, float, float]:
    """Walk hourly candles after entry, apply the plan's ladder.

    Rules (long):
    - If any bar's low ``<= plan.hard_stop`` before target_1 fires, the
      trade exits at ``hard_stop`` (worst-case fill assumption).
    - If target_1 fires (bar high ``>= plan.target_1``), take 50% off and
      move stop to entry. Continue with residual 50%.
    - Under residual, if a subsequent bar's low ``<= entry_price``, exit
      residual at entry (breakeven on residual). If target_2 fires, exit
      residual at target_2.
    - Otherwise the time-stop closes the trade at the bar whose close_time
      is ``>= entry_time + plan.time_stop_seconds``, at that bar's close.

    Returns the exit candle, the effective exit price (weighted across
    partial fills), the exit reason string, MFE and MAE in percentage
    points from entry.
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
) -> SimulatedTrade:
    exit_candle, exit_price, reason, mfe, mae = _walk_exit(
        hourly_after_entry, plan, entry_time=entry_time
    )
    gross_pct = (exit_price / plan.entry_price - 1.0) * 100.0
    cost = round_trip_cost_pct(
        entry_time=entry_time,
        exit_time=exit_candle.close_time,
        funding_history=funding_history,
        taker_fee_bps=taker_fee_bps,
        slippage_bps=slippage_bps,
    )
    net_pct = gross_pct * scale - cost.total_pct
    return SimulatedTrade(
        symbol=symbol,
        entry_time=entry_time,
        exit_time=exit_candle.close_time,
        entry_price=plan.entry_price,
        exit_price=exit_price,
        plan=plan,
        scale=scale,
        gross_return_pct=gross_pct * scale,
        cost=cost,
        net_return_pct=net_pct,
        mfe_pct=mfe * scale,
        mae_pct=mae * scale,
        exit_reason=reason,
    )


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
    """Chronological walk-forward folds with an embargo between train and test.

    Each fold's train window is expanding (0 → fold_end_of_train), test
    window is the following slab after ``embargo_days``. Folds that would
    have a train slab shorter than ``min_train_days`` are skipped.
    """

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
# Metrics
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


def _stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _max_drawdown_pct(returns: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for r in returns:
        equity += r
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def compute_fold_metrics(fold_name: str, trades: Sequence[SimulatedTrade]) -> FoldMetrics:
    if not trades:
        return FoldMetrics(
            fold=fold_name,
            n_trades=0,
            hit_rate=0.0,
            expectancy_pct=0.0,
            mean_gross_pct=0.0,
            mean_cost_pct=0.0,
            mean_net_pct=0.0,
            stdev_net_pct=0.0,
            sharpe_annualized=0.0,
            max_drawdown_pct=0.0,
            mean_mfe_pct=0.0,
            mean_mae_pct=0.0,
            exit_reason_breakdown={},
        )
    net = [trade.net_return_pct for trade in trades]
    gross = [trade.gross_return_pct for trade in trades]
    costs = [trade.cost.total_pct for trade in trades]
    wins = sum(1 for r in net if r > 0)
    mfe = [t.mfe_pct for t in trades]
    mae = [t.mae_pct for t in trades]
    stdev_net = _stdev(net)
    mean_net = sum(net) / len(net)
    # Approximate trade frequency: annualize by trades-per-year assuming
    # the fold covers len(trades) trades linearly. When we know the fold's
    # calendar span we would use that; here Sharpe uses the per-trade
    # standard deviation scaled by sqrt(TRADING_DAYS_PER_YEAR / avg_hold).
    holds_days = [(t.exit_time - t.entry_time) / 86400.0 for t in trades]
    avg_hold = sum(holds_days) / len(holds_days) if holds_days else 1.0
    trades_per_year = TRADING_DAYS_PER_YEAR / max(avg_hold, 1e-6)
    sharpe = (mean_net / stdev_net) * math.sqrt(trades_per_year) if stdev_net > 0 else 0.0
    breakdown: dict[str, int] = {}
    for trade in trades:
        breakdown[trade.exit_reason] = breakdown.get(trade.exit_reason, 0) + 1
    return FoldMetrics(
        fold=fold_name,
        n_trades=len(trades),
        hit_rate=wins / len(trades),
        expectancy_pct=mean_net,
        mean_gross_pct=sum(gross) / len(gross),
        mean_cost_pct=sum(costs) / len(costs),
        mean_net_pct=mean_net,
        stdev_net_pct=stdev_net,
        sharpe_annualized=sharpe,
        max_drawdown_pct=_max_drawdown_pct(net),
        mean_mfe_pct=sum(mfe) / len(mfe),
        mean_mae_pct=sum(mae) / len(mae),
        exit_reason_breakdown=breakdown,
    )


# ---------------------------------------------------------------------------
# Main harness entrypoint
# ---------------------------------------------------------------------------


@dataclass
class BacktestReport:
    symbol: str
    params: TsmomParams
    folds: list[FoldMetrics] = field(default_factory=list)
    aggregate: FoldMetrics | None = None
    total_days: int = 0
    daily_bars: int = 0
    hourly_bars: int = 0
    trades: int = 0
    embargo_days: int = 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "params": asdict(self.params),
            "total_days": self.total_days,
            "daily_bars": self.daily_bars,
            "hourly_bars": self.hourly_bars,
            "trades": self.trades,
            "embargo_days": self.embargo_days,
            "folds": [asdict(fold) for fold in self.folds],
            "aggregate": asdict(self.aggregate) if self.aggregate else None,
            "can_authorize_trade": False,
        }


def _hourly_after(entry_time: int, hourly: Sequence[Candle], *, horizon_hours: int) -> list[Candle]:
    return [
        candle
        for candle in hourly
        if entry_time < candle.close_time <= entry_time + horizon_hours * 3600
    ]


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
    horizon_hours: int = 96,
) -> BacktestReport:
    """Run a purged walk-forward backtest and return per-fold + aggregate metrics."""

    if len(daily) < params.lookback_days + params.realized_vol_lookback_days + 2:
        raise ValueError("not enough daily bars for the configured lookbacks")
    if not hourly:
        raise ValueError("hourly bars required for exit simulation")

    trades: list[SimulatedTrade] = []
    min_start = max(
        params.lookback_days,
        params.realized_vol_lookback_days,
        params.atr_lookback_days,
    )
    for i in range(min_start, len(daily) - 1):
        decision = evaluate_tsmom(
            daily[: i + 1],
            symbol=symbol,
            decision_at=daily[i].close_time,
            params=params,
        )
        if decision.signal is not TsmomSignal.LONG or decision.plan is None:
            continue
        post = _hourly_after(daily[i].close_time, hourly, horizon_hours=horizon_hours)
        if not post:
            continue
        trades.append(
            simulate_trade(
                symbol=symbol,
                plan=decision.plan,
                scale=decision.position_scale,
                entry_time=daily[i].close_time,
                hourly_after_entry=post,
                funding_history=funding,
                taker_fee_bps=taker_fee_bps,
                slippage_bps=slippage_bps,
            )
        )

    total_days = len(daily)
    folds = enumerate_folds(
        total_days=total_days,
        n_folds=n_folds,
        embargo_days=embargo_days,
        min_train_days=min_start,
    )
    fold_metrics: list[FoldMetrics] = []
    for fold in folds:
        window_start = daily[fold.test_start_idx].close_time if fold.test_start_idx < total_days else 0
        window_end_idx = min(fold.test_end_idx, total_days) - 1
        window_end = daily[window_end_idx].close_time if 0 <= window_end_idx < total_days else 0
        fold_trades = [
            trade
            for trade in trades
            if window_start <= trade.entry_time < window_end
        ]
        fold_metrics.append(compute_fold_metrics(fold.name, fold_trades))

    aggregate = compute_fold_metrics("aggregate_all_folds", trades)
    return BacktestReport(
        symbol=symbol.upper(),
        params=params,
        folds=fold_metrics,
        aggregate=aggregate,
        total_days=total_days,
        daily_bars=len(daily),
        hourly_bars=len(hourly),
        trades=len(trades),
        embargo_days=embargo_days,
    )


def write_report(report: BacktestReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")


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


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the TSMOM walk-forward backtest on stored parquet data."
    )
    parser.add_argument("symbol", help="e.g. BTCUSDT")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("research/data/binance_perp"),
        help="directory containing SYMBOL_klines_1d.parquet etc.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("research/data/backtest_report.json"),
    )
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--embargo-days", type=int, default=3)
    parser.add_argument("--horizon-hours", type=int, default=96)
    args = parser.parse_args(list(argv) if argv is not None else None)

    daily = _load_candles(args.data_dir / f"{args.symbol.upper()}_klines_1d.parquet")
    hourly = _load_candles(args.data_dir / f"{args.symbol.upper()}_klines_1h.parquet")
    funding_path = args.data_dir / f"{args.symbol.upper()}_funding.parquet"
    funding = _load_funding(funding_path) if funding_path.exists() else None

    report = run_backtest(
        symbol=args.symbol,
        daily=daily,
        hourly=hourly,
        funding=funding,
        n_folds=args.folds,
        embargo_days=args.embargo_days,
        horizon_hours=args.horizon_hours,
    )
    write_report(report, args.out)
    print(f"wrote {args.out}")
    print(f"trades: {report.trades}")
    if report.aggregate:
        print(
            f"aggregate: net={report.aggregate.mean_net_pct:.3f}% "
            f"sharpe={report.aggregate.sharpe_annualized:.2f} "
            f"hit={report.aggregate.hit_rate:.1%} "
            f"maxdd={report.aggregate.max_drawdown_pct:.2f}%"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
