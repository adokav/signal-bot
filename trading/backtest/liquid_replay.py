"""Historical replay of the live Liquid-100 long radar (technical layer).

The radar shows the user a "top 3 long" list out of the 100 most traded
pairs. Its weights and thresholds were chosen by hand and never tested.
This replays the *production* functions over a survivorship-free Binance
spot universe (``trading.data.binance_universe``) and measures what its
picks did next.

Faithfulness rules:

- **Same code.** ``select_liquid_universe``, ``build_market_context``,
  ``select_enrichment_universe``, ``calculate_long_metrics`` and
  ``score_technical_long`` from ``acce_unified.liquid_long`` run unchanged.
- **Point in time.** At each 15-minute close the universe is ranked by the
  trailing 24h quote volume of bars already closed; metrics use the last 96
  closed 15m bars (the live adapter fetches 97 and drops the forming one);
  a pair with any missing bar in that window is unavailable, as a failed
  live fetch would be.
- **What is not replayed, stated in every report.** The supply score
  (CoinGecko, 25% of the live final score) has no point-in-time history, so
  the min-score threshold cannot be applied: the replay ranks by technical
  score, like the engine's pre-supply shortlist. The order book is not
  available, so a fixed spread is assumed. Binance spot stands in for MEXC.

Measured quantity (no stop or target exists for this radar): forward return
from the next bar's open to the close ``H`` hours later, net of costs, and
the same return *in excess of the equal-weight universe* over the same
window — the second number separates selection skill from market beta
(spec §20, §42). Delisted pairs exit at their last traded close.

No order authority anywhere (AGENTS.md §4, §10).
"""

from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

from acce_unified.liquid_long import (
    build_market_context,
    calculate_long_metrics,
    score_technical_long,
    select_enrichment_universe,
    select_liquid_universe,
)
from acce_unified.models import CexTicker
from trading.data.binance_universe import coverage_end


BAR_SECONDS = 900
DAY_BARS = 96
METRIC_BARS = 96

REPO_ROOT = Path(__file__).resolve().parents[2]
# Everything that selects, scores or measures a pick. A change to any of
# these is a new trial (AGENTS.md §6-7).
FINGERPRINT_FILES = (
    "acce_unified/liquid_long.py",
    "acce_unified/cex.py",
    "acce_unified/models.py",
    "trading/data/binance_universe.py",
    "trading/backtest/liquid_replay.py",
)

GROUPS = ("TOP3", "ALL_READY")
DECISION_HORIZONS_H = (4, 24)
DIAGNOSTIC_HORIZONS_H = (1, 4, 12, 24, 72)
# Pre-declared multiple-testing budget: 2 groups x 2 horizons.
FAMILY_ALPHA = 0.05 / (len(GROUPS) * len(DECISION_HORIZONS_H))
MIN_OBSERVATIONS = 300
REGIME_CODES = ("RISK_ON", "NEUTRAL", "RISK_OFF", "CAPITULATION")


@dataclass(frozen=True)
class RadarParams:
    """Live configuration (render.yaml) plus the replay's stated assumptions."""

    universe_size: int = 100
    min_quote_volume: float = 1_000_000.0
    max_24h_drawdown_pct: float = -8.0
    max_24h_gain_pct: float = 25.0
    max_spread_bps: float = 35.0
    top_n: int = 3
    assumed_spread_bps: float = 10.0
    min_benchmark_members: int = 50


@dataclass(frozen=True)
class ReplayCosts:
    fee_bps_per_side: float = 5.0
    slippage_bps_per_side: float = 5.0

    def round_trip_pct(self, *, fee_mult: float = 1.0, slip_mult: float = 1.0) -> float:
        return 2.0 * (self.fee_bps_per_side * fee_mult + self.slippage_bps_per_side * slip_mult) / 100.0


# ---------------------------------------------------------------------------
# Market matrices
# ---------------------------------------------------------------------------


@dataclass
class Market:
    symbols: list[str]
    grid_open: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    quote_volume: np.ndarray
    qv24: np.ndarray
    chg24: np.ndarray
    last_index: np.ndarray       # last grid index with data, per symbol
    coverage_end: np.ndarray     # last grid index the download plan covered, per symbol
    rejected_rows: int = 0
    index_of: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index_of = {symbol: k for k, symbol in enumerate(self.symbols)}

    @property
    def n_grid(self) -> int:
        return len(self.grid_open)


COLUMNS = ("open_time", "close_time", "open", "high", "low", "close", "quote_volume")


def clean_columns(cols: Mapping[str, np.ndarray]) -> tuple[dict[str, np.ndarray], int]:
    """Drop malformed, misaligned and duplicate 15m rows (never repaired); returns (columns, rejected)."""

    ot = np.asarray(cols["open_time"], dtype=np.int64)
    ct = np.asarray(cols["close_time"], dtype=np.int64)
    o, h, low, c, qv = (np.asarray(cols[k], dtype=float) for k in ("open", "high", "low", "close", "quote_volume"))
    with np.errstate(invalid="ignore"):
        ok = (
            (ot % BAR_SECONDS == 0) & ((ct - ot == BAR_SECONDS - 1) | (ct - ot == BAR_SECONDS))
            & np.isfinite(o) & np.isfinite(h) & np.isfinite(low) & np.isfinite(c) & np.isfinite(qv)
            & (np.minimum(np.minimum(o, h), np.minimum(low, c)) > 0)
            & (h >= np.maximum(o, c)) & (low <= np.minimum(o, c)) & (qv >= 0)
        )
    idx = np.nonzero(ok)[0]
    order = idx[np.argsort(ot[idx], kind="stable")]
    _, first = np.unique(ot[order], return_index=True)
    keep = order[first]
    out = {"open_time": ot[keep], "open": o[keep], "high": h[keep], "low": low[keep],
           "close": c[keep], "quote_volume": qv[keep]}
    return out, int(len(ot) - len(keep))


def build_market(
    columns_by_symbol: Mapping[str, Mapping[str, np.ndarray]],
    *,
    coverage_end_time: Mapping[str, int] | None = None,
) -> Market:
    """Align per-symbol 15m columns on one grid (NaN where a bar is missing)."""

    cleaned: dict[str, dict[str, np.ndarray]] = {}
    rejected = 0
    for symbol, cols in columns_by_symbol.items():
        kept, dropped = clean_columns(cols)
        rejected += dropped
        if len(kept["open_time"]):
            cleaned[symbol] = kept
    if not cleaned:
        raise ValueError("no usable candles")
    symbols = sorted(cleaned)
    start = min(int(c["open_time"][0]) for c in cleaned.values())
    end = max(int(c["open_time"][-1]) for c in cleaned.values())
    grid_open = np.arange(start, end + BAR_SECONDS, BAR_SECONDS, dtype=np.int64)
    shape = (len(symbols), len(grid_open))
    names = ("open", "high", "low", "close", "quote_volume")
    mats = {name: np.full(shape, np.nan) for name in names}
    last_index = np.full(len(symbols), -1, dtype=np.int64)
    coverage_end = np.full(len(symbols), -1, dtype=np.int64)
    for s, symbol in enumerate(symbols):
        cols = cleaned.pop(symbol)
        idx = (cols["open_time"] - start) // BAR_SECONDS
        for name in names:
            mats[name][s, idx] = cols[name]
        last_index[s] = idx[-1]
        cov = (coverage_end_time or {}).get(symbol)
        coverage_end[s] = idx[-1] if cov is None else min(len(grid_open) - 1, (int(cov) - start) // BAR_SECONDS)
    qv24, chg24 = _rolling_24h(mats["quote_volume"], mats["close"])
    return Market(symbols, grid_open, mats["open"], mats["high"], mats["low"], mats["close"],
                  mats["quote_volume"], qv24, chg24, last_index, coverage_end, rejected)


def rows_to_columns(rows: Iterable[Sequence[float]]) -> dict[str, np.ndarray]:
    """(open_time, close_time, open, high, low, close, quote_volume) tuples to columns (tests, small data)."""

    rows = list(rows)
    return {name: np.array([r[k] for r in rows]) for k, name in enumerate(COLUMNS)}


def _rolling_24h(quote_volume: np.ndarray, close: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Trailing 24h quote volume (NaN unless all 96 bars exist) and 24h % change."""

    qv24 = np.full(quote_volume.shape, np.nan)
    chg24 = np.full(close.shape, np.nan)
    for s in range(quote_volume.shape[0]):  # row by row keeps peak memory at one row of temporaries
        present = np.isfinite(quote_volume[s])
        csum = np.concatenate([[0.0], np.cumsum(np.where(present, quote_volume[s], 0.0))])
        ccount = np.concatenate([[0], np.cumsum(present)])
        if len(present) >= DAY_BARS:
            window_count = ccount[DAY_BARS:] - ccount[:-DAY_BARS]
            qv24[s, DAY_BARS - 1:] = np.where(window_count == DAY_BARS, csum[DAY_BARS:] - csum[:-DAY_BARS], np.nan)
        if len(present) > DAY_BARS:
            with np.errstate(invalid="ignore", divide="ignore"):
                chg24[s, DAY_BARS:] = (close[s, DAY_BARS:] / close[s, :-DAY_BARS] - 1.0) * 100.0
    return qv24, chg24


def load_market(data_dir: Path) -> tuple[Market, dict]:
    import pandas as pd

    manifest = json.loads((data_dir / "manifest.json").read_text("utf-8"))
    columns: dict[str, dict[str, np.ndarray]] = {}
    coverage: dict[str, int] = {}
    for symbol, info in manifest["symbols"].items():
        frame = pd.read_parquet(data_dir / "15m" / f"{symbol}.parquet", columns=list(COLUMNS))
        columns[symbol] = {name: frame[name].to_numpy() for name in COLUMNS}
        year, month = (int(x) for x in info["months"][-1].split("-"))
        coverage[symbol] = coverage_end((year, month)) - (BAR_SECONDS - 1)  # open of the last covered bar
    return build_market(columns, coverage_end_time=coverage), manifest


# ---------------------------------------------------------------------------
# Phase 1: the live radar at every 15m close (parallel)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    universe: tuple[int, ...]
    regime: str
    top: tuple[int, ...]
    ready: tuple[int, ...]


def _ticker(market: Market, s: int, i: int, spread_bps: float) -> CexTicker:
    last = float(market.close[s, i])
    half = last * spread_bps / 2.0 / 10_000.0
    return CexTicker(
        symbol=market.symbols[s], last_price=last, change_pct=float(market.chg24[s, i]),
        quote_volume=float(market.qv24[s, i]), venue="BINANCE_SPOT_REPLAY",
        bid_price=last - half, ask_price=last + half,
    )


def metrics_at(market: Market, s: int, i: int) -> dict:
    """Live ``calculate_long_metrics`` on the 96 bars closed at grid index ``i``."""

    a = i - METRIC_BARS + 1
    if a < 0:
        return {"status": "INSUFFICIENT_KLINES", "can_authorize_trade": False}
    close = market.close[s, a:i + 1]
    if not np.isfinite(close).all():
        return {"status": "PROVIDER_UNAVAILABLE", "can_authorize_trade": False}
    opens = market.grid_open[a:i + 1].tolist()
    quote_volume = market.quote_volume[s, a:i + 1].tolist()
    # Column 5 (base volume) is read by the live function only as a fallback
    # when the quote volume (column 7) is missing, which never happens here.
    rows = list(zip(
        opens, market.open[s, a:i + 1].tolist(), market.high[s, a:i + 1].tolist(),
        market.low[s, a:i + 1].tolist(), close.tolist(), quote_volume,
        [t + BAR_SECONDS - 1 for t in opens], quote_volume,
    ))
    # The live adapter requests 97 rows and drops the last (forming) one;
    # a copy stands in for it so the live function sees the same shape.
    rows.append(rows[-1])
    return calculate_long_metrics(rows)


def evaluate_step(market: Market, i: int, params: RadarParams) -> Step | None:
    available = np.isfinite(market.qv24[:, i]) & np.isfinite(market.chg24[:, i]) & np.isfinite(market.close[:, i])
    tickers = [_ticker(market, int(s), i, params.assumed_spread_bps) for s in np.nonzero(available)[0]]
    universe = select_liquid_universe(tickers, size=params.universe_size, min_quote_volume=params.min_quote_volume)
    if not any(t.symbol == "BTCUSDT" for t in universe):
        # Live MEXC always lists BTC; the live context would silently read a
        # missing BTC as a 0% day. Here it can only mean missing data: skip.
        return None
    context = build_market_context(universe)
    enrichment = select_enrichment_universe(
        universe, size=params.universe_size, max_drawdown_pct=params.max_24h_drawdown_pct,
        max_gain_pct=params.max_24h_gain_pct, max_spread_bps=params.max_spread_bps,
    )
    index_of = market.index_of
    ranks = {t.symbol: k for k, t in enumerate(universe, 1)}
    ready = []
    for t in enrichment:
        s = index_of[t.symbol]
        technical = score_technical_long(
            t, metrics_at(market, s, i), liquidity_rank=ranks[t.symbol],
            max_drawdown_pct=params.max_24h_drawdown_pct, max_gain_pct=params.max_24h_gain_pct,
            max_spread_bps=params.max_spread_bps,
        )
        if technical.get("status") == "READY":
            ready.append((int(technical.get("score") or 0), t.quote_volume, s))
    ready.sort(key=lambda row: (row[0], row[1]), reverse=True)
    regime = str(context.get("regime") or "UNKNOWN")
    shown = [] if regime == "CAPITULATION" else ready  # the live ranker returns nothing in CAPITULATION
    return Step(
        universe=tuple(index_of[t.symbol] for t in universe),
        regime=regime,
        top=tuple(row[2] for row in shown[: params.top_n]),
        ready=tuple(row[2] for row in shown),
    )


_WORKER: tuple[Market, RadarParams] | None = None


def _evaluate_chunk(bounds: tuple[int, int]) -> list[Step | None]:
    if _WORKER is None:
        raise RuntimeError("market not initialised")
    market, params = _WORKER
    return [evaluate_step(market, i, params) for i in range(*bounds)]


def evaluate(market: Market, params: RadarParams, *, workers: int = 1, chunks: int | None = None,
             progress: bool = False) -> list[Step | None]:
    global _WORKER
    n = market.n_grid
    n_chunks = max(1, chunks or workers * 8)
    size = max(1, math.ceil(n / n_chunks))
    bounds = [(a, min(n, a + size)) for a in range(0, n, size)]
    _WORKER = (market, params)
    steps: list[Step | None] = []
    pool = None
    try:
        if workers <= 1:
            parts: Iterable = map(_evaluate_chunk, bounds)
        else:
            pool = multiprocessing.get_context("fork").Pool(workers)
            parts = pool.imap(_evaluate_chunk, bounds)
        for k, part in enumerate(parts, 1):
            steps.extend(part)
            if progress:
                print(f"phase 1: {k}/{len(bounds)} chunks", flush=True)
    finally:
        if pool is not None:
            pool.terminate()
            pool.join()
        _WORKER = None
    return steps


# ---------------------------------------------------------------------------
# Phase 2: events and outcomes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    group: str
    horizon_h: int
    step: int
    symbol: str
    decided_at: int
    regime: str
    gross_pct: float
    benchmark_pct: float
    delisted_exit: bool


def entry_events(steps: Sequence[Step | None], group: str) -> list[tuple[int, int]]:
    """(step, symbol) whenever a symbol newly enters the shown list; a skipped scan keeps the last list."""

    previous: set[int] = set()
    out = []
    for i, step in enumerate(steps):
        if step is None:
            continue
        current = set(step.top if group == "TOP3" else step.ready)
        out.extend((i, s) for s in sorted(current - previous))
        previous = current
    return out


def forward_returns(market: Market, members: Sequence[int], i: int, bars: int) -> tuple[np.ndarray, np.ndarray]:
    """Gross % returns from the next bar's open to the close ``bars`` later (NaN when unknowable).

    Data that stops well before the downloaded coverage ends is a delisting
    or trading halt: the position exits at the last traded close. A gap the
    data later resumes from, or the edge of the download, is unknowable and
    is not guessed. Returns (returns, delisted_exit flags).
    """

    members = np.asarray(members, dtype=np.int64)
    entry_i, exit_i = i + 1, i + bars
    if exit_i >= market.n_grid or len(members) == 0:
        return np.full(len(members), np.nan), np.zeros(len(members), dtype=bool)
    entry = market.open[members, entry_i]
    exit_price = market.close[members, exit_i]
    last = market.last_index[members]
    delisted = (
        ~np.isfinite(exit_price) & (last >= entry_i) & (last < exit_i)
        & (last < market.coverage_end[members] - DAY_BARS)
    )
    exit_price = np.where(delisted, market.close[members, np.clip(last, 0, None)], exit_price)
    with np.errstate(invalid="ignore", divide="ignore"):
        returns = (exit_price / entry - 1.0) * 100.0
    return returns, delisted & np.isfinite(returns)


def forward_return(market: Market, s: int, i: int, bars: int) -> tuple[float, bool] | None:
    returns, delisted = forward_returns(market, [s], i, bars)
    return None if not math.isfinite(returns[0]) else (float(returns[0]), bool(delisted[0]))


def observations(
    market: Market,
    steps: Sequence[Step | None],
    params: RadarParams,
    *,
    group: str,
    horizon_h: int,
) -> tuple[list[Observation], int]:
    """Thinned observations (one open per symbol at a time) and the unresolvable count."""

    bars = horizon_h * 3600 // BAR_SECONDS
    next_allowed: dict[int, int] = {}
    benchmarks: dict[int, float | None] = {}
    out: list[Observation] = []
    unresolvable = 0
    for i, s in entry_events(steps, group):
        if i < next_allowed.get(s, -1):
            continue
        step = steps[i]
        if step is None:  # pragma: no cover - entry_events never yields skipped steps
            continue
        if i not in benchmarks:
            returns, _ = forward_returns(market, step.universe, i, bars)
            finite = returns[np.isfinite(returns)]
            benchmarks[i] = float(finite.mean()) if len(finite) >= params.min_benchmark_members else None
        own = forward_return(market, s, i, bars)
        bench = benchmarks[i]
        if own is None or bench is None:
            unresolvable += 1
            continue
        next_allowed[s] = i + bars
        out.append(Observation(
            group=group, horizon_h=horizon_h, step=i, symbol=market.symbols[s],
            decided_at=int(market.grid_open[i]) + BAR_SECONDS - 1, regime=step.regime,
            gross_pct=own[0], benchmark_pct=bench, delisted_exit=own[1],
        ))
    return out, unresolvable


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def day_cluster_ci(values: Sequence[float], days: Sequence[int], *, alpha: float,
                   n_resamples: int = 4000, seed: int = 0) -> tuple[float, float] | None:
    """Percentile CI of the mean, resampling whole UTC days.

    Picks made on the same day share the market move; resampling days keeps
    that dependence instead of pretending every pick is independent.
    """

    if len(values) < 2:
        return None
    order: dict[int, int] = {}
    labels = np.array([order.setdefault(int(d), len(order)) for d in days])
    sums = np.bincount(labels, weights=np.asarray(values, dtype=float))
    counts = np.bincount(labels).astype(float)
    if len(sums) < 2:
        return None
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(sums), size=(n_resamples, len(sums)))
    means = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


@dataclass(frozen=True)
class GroupStats:
    name: str
    n: int
    unresolvable: int
    delisted_exits: int
    mean_gross_pct: float | None
    mean_net_pct: float | None
    mean_benchmark_pct: float | None
    mean_excess_pct: float | None
    excess_hit_rate: float | None
    excess_ci95: tuple[float, float] | None
    excess_ci_family: tuple[float, float] | None
    stressed_mean_net_pct: float | None
    stressed_mean_excess_pct: float | None
    first_half_excess_pct: float | None
    second_half_excess_pct: float | None
    verdict: str

    def to_dict(self) -> dict:
        return asdict(self)


def _mean(values: Sequence[float]) -> float | None:
    return float(np.mean(values)) if len(values) else None


def group_stats(name: str, obs: Sequence[Observation], costs: ReplayCosts, *, unresolvable: int = 0,
                decision_group: bool = True, seed: int = 0) -> GroupStats:
    """Pre-declared rules: PASS_CANDIDATE needs >= 300 picks, a family-wise day-cluster lower bound
    of net excess return above zero, positive net and excess return under stressed costs, and
    positive net excess in both chronological halves."""

    obs = sorted(obs, key=lambda o: (o.decided_at, o.symbol))
    base, stress = costs.round_trip_pct(), costs.round_trip_pct(fee_mult=1.5, slip_mult=2.0)
    gross = [o.gross_pct for o in obs]
    bench = [o.benchmark_pct for o in obs]
    net = [g - base for g in gross]
    excess = [n - b for n, b in zip(net, bench)]
    s_net = [g - stress for g in gross]
    s_excess = [n - b for n, b in zip(s_net, bench)]
    days = [o.decided_at // 86_400 for o in obs]
    half = len(obs) // 2
    ci95 = day_cluster_ci(excess, days, alpha=0.05, n_resamples=2000, seed=seed)
    ci_family = day_cluster_ci(excess, days, alpha=FAMILY_ALPHA, seed=seed)
    first, second = _mean(excess[:half]), _mean(excess[half:])
    if not decision_group:
        verdict = "DIAGNOSTIC_ONLY"
    elif ci_family is not None and ci_family[1] < 0:
        verdict = "NEGATIVE"
    elif len(obs) < MIN_OBSERVATIONS:
        verdict = "INSUFFICIENT"
    elif (
        ci_family is not None and ci_family[0] > 0
        and _mean(s_excess) > 0 and _mean(s_net) > 0
        and first is not None and first > 0 and second is not None and second > 0
    ):
        verdict = "PASS_CANDIDATE"
    else:
        verdict = "NO_EDGE"
    return GroupStats(
        name=name, n=len(obs), unresolvable=unresolvable,
        delisted_exits=sum(o.delisted_exit for o in obs),
        mean_gross_pct=_mean(gross), mean_net_pct=_mean(net), mean_benchmark_pct=_mean(bench),
        mean_excess_pct=_mean(excess),
        excess_hit_rate=(sum(e > 0 for e in excess) / len(excess)) if excess else None,
        excess_ci95=ci95, excess_ci_family=ci_family,
        stressed_mean_net_pct=_mean(s_net), stressed_mean_excess_pct=_mean(s_excess),
        first_half_excess_pct=first, second_half_excess_pct=second, verdict=verdict,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def engine_fingerprint(repo_root: Path = REPO_ROOT) -> str:
    digest = hashlib.sha256()
    for name in FINGERPRINT_FILES:
        digest.update(name.encode())
        digest.update((repo_root / name).read_bytes())
    return digest.hexdigest()[:16]


@dataclass
class ReplayReport:
    start_time: int
    end_time: int
    symbols: int
    steps: int
    evaluated: int
    skipped: int
    mean_universe_size: float
    rejected_rows: int
    params: RadarParams
    costs: ReplayCosts
    engine_fingerprint: str
    decision: dict[str, GroupStats]
    by_regime: dict[str, GroupStats]
    by_year: dict[str, GroupStats]
    decay: dict[str, GroupStats]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "start_time": self.start_time, "end_time": self.end_time, "symbols": self.symbols,
            "steps": self.steps, "evaluated": self.evaluated, "skipped": self.skipped,
            "mean_universe_size": self.mean_universe_size, "rejected_rows": self.rejected_rows,
            "params": asdict(self.params), "costs": asdict(self.costs),
            "engine_fingerprint": self.engine_fingerprint,
            "decision": {k: v.to_dict() for k, v in self.decision.items()},
            "by_regime": {k: v.to_dict() for k, v in self.by_regime.items()},
            "by_year": {k: v.to_dict() for k, v in self.by_year.items()},
            "decay": {k: v.to_dict() for k, v in self.decay.items()},
            "notes": list(self.notes),
            "can_authorize_trade": False,
        }


def run_replay(market: Market, *, params: RadarParams = RadarParams(), costs: ReplayCosts = ReplayCosts(),
               workers: int = 1, fingerprint: str = "unknown", progress: bool = False,
               ) -> tuple[ReplayReport, list[Observation]]:
    steps = evaluate(market, params, workers=workers, progress=progress)
    evaluated = [s for s in steps if s is not None]
    all_obs: list[Observation] = []
    decision: dict[str, GroupStats] = {}
    decay: dict[str, GroupStats] = {}
    for group in GROUPS:
        for horizon in sorted(set(DECISION_HORIZONS_H) | set(DIAGNOSTIC_HORIZONS_H)):
            obs, unresolvable = observations(market, steps, params, group=group, horizon_h=horizon)
            name = f"{group}@{horizon}h"
            if horizon in DECISION_HORIZONS_H:
                decision[name] = group_stats(name, obs, costs, unresolvable=unresolvable)
                all_obs.extend(obs)
            decay[name] = group_stats(name, obs, costs, unresolvable=unresolvable, decision_group=False)

    def sliced(key) -> dict[str, GroupStats]:
        buckets: dict[str, list[Observation]] = {}
        for o in all_obs:
            buckets.setdefault(f"{o.group}@{o.horizon_h}h {key(o)}", []).append(o)
        return {k: group_stats(k, v, costs, decision_group=False) for k, v in sorted(buckets.items())}

    notes = [
        "Binance spot, MEXC spot yerine kullanıldı: MEXC'nin ilk 100'ü Binance'tekinden farklı coinler içerebilir.",
        "Arz puanı (CoinGecko, canlı nihai puanın %25'i) ve min puan 64 eşiği geçmişe dönük uygulanamadı; sıralama teknik puana göre.",
        f"Spread sabit {params.assumed_spread_bps:g} bp varsayıldı (geçmiş emir defteri yok); spread kapısı test edilemedi.",
        "Karar aralığı 15 dakika (canlı tarama 2 dakika; metrikler zaten 15 dakikalık kapanmış mumlarla hesaplanıyor).",
        "Aday ön-filtresi günlük hacim sırası ≤150; gerçek ilk 100 her adımda son 24 saatlik hacimle yeniden hesaplandı.",
        "Giriş bir sonraki 15 dakikalık mumun açılışından; stop/hedef yok, sabit süreli çıkış. Kalkan coinler son işlem fiyatından çıkar.",
        f"Fazla getiri = net getiri − eşit ağırlıklı evren getirisi; güven aralığı gün bazlı küme bootstrap (Bonferroni α={FAMILY_ALPHA:g}).",
    ]
    if not evaluated:
        notes.append("UYARI: hiçbir adım değerlendirilemedi.")
    skipped = len(steps) - len(evaluated)
    if skipped > 0.02 * max(1, len(steps) - DAY_BARS):
        notes.append("UYARI: adımların %2'sinden fazlası veri eksikliği nedeniyle atlandı.")
    if market.rejected_rows:
        notes.append(f"UYARI: {market.rejected_rows} bozuk/tekrarlı mum satırı atıldı (onarılmadı).")
    report = ReplayReport(
        start_time=int(market.grid_open[0]), end_time=int(market.grid_open[-1]) + BAR_SECONDS - 1,
        symbols=len(market.symbols), steps=len(steps), evaluated=len(evaluated), skipped=skipped,
        mean_universe_size=float(np.mean([len(s.universe) for s in evaluated])) if evaluated else 0.0,
        rejected_rows=market.rejected_rows, params=params, costs=costs, engine_fingerprint=fingerprint,
        decision=decision,
        by_regime=sliced(lambda o: o.regime),
        by_year=sliced(lambda o: datetime.fromtimestamp(o.decided_at, tz=timezone.utc).year),
        decay=decay, notes=notes,
    )
    return report, all_obs


def write_observations(obs: Sequence[Observation], path: Path) -> None:
    import csv
    import gzip

    columns = list(Observation.__dataclass_fields__)
    with gzip.open(path, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for o in obs:
            writer.writerow([getattr(o, c) for c in columns])


def _fmt(value, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def print_summary(report: ReplayReport) -> None:
    print(f"symbols={report.symbols} steps={report.steps} evaluated={report.evaluated} skipped={report.skipped} "
          f"universe={report.mean_universe_size:.1f} rejected_rows={report.rejected_rows} "
          f"engine={report.engine_fingerprint}")
    print(f"{'group':<34}{'n':>6}{'unres':>6}{'gross%':>8}{'net%':>8}{'bench%':>8}{'excess%':>9}"
          f"{'hit':>6}{'famCI':>18}{'s.net%':>8}{'s.exc%':>8}{'h1':>7}{'h2':>7}  verdict")
    for g in (*report.decision.values(), *report.decay.values(), *report.by_regime.values(), *report.by_year.values()):
        ci = "n/a" if g.excess_ci_family is None else f"[{g.excess_ci_family[0]:+.2f},{g.excess_ci_family[1]:+.2f}]"
        print(f"{g.name:<34}{g.n:>6}{g.unresolvable:>6}{_fmt(g.mean_gross_pct, '+.2f'):>8}"
              f"{_fmt(g.mean_net_pct, '+.2f'):>8}{_fmt(g.mean_benchmark_pct, '+.2f'):>8}"
              f"{_fmt(g.mean_excess_pct, '+.3f'):>9}{_fmt(g.excess_hit_rate, '.2f'):>6}{ci:>18}"
              f"{_fmt(g.stressed_mean_net_pct, '+.2f'):>8}{_fmt(g.stressed_mean_excess_pct, '+.2f'):>8}"
              f"{_fmt(g.first_half_excess_pct, '+.2f'):>7}{_fmt(g.second_half_excess_pct, '+.2f'):>7}  {g.verdict}")
    for note in report.notes:
        print(f"note: {note}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


REPLAY_FAMILY = "liquid_100_long_radar"


def replay_trial_params(*, fingerprint: str, params: RadarParams, costs: ReplayCosts) -> dict:
    return {
        "engine_fingerprint": fingerprint,
        "cadence_seconds": BAR_SECONDS,
        "radar": asdict(params),
        "costs": asdict(costs),
        "groups": list(GROUPS),
        "decision_horizons_h": list(DECISION_HORIZONS_H),
        "outcome": "next_open_to_close_net_excess_vs_equal_weight_universe",
    }


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse

    from trading.research.report import StrategyReport
    from trading.research.robustness import TrialRecord, TrialRegistry, trial_id_for
    from trading.strategies.liquid_dossier import (
        LIQUID_BLOW_UP_SCENARIOS,
        LIQUID_FRAGILITY_ANSWERS,
        LIQUID_HYPOTHESIS,
    )

    parser = argparse.ArgumentParser(description="Replay the live Liquid-100 long radar.")
    parser.add_argument("--data-dir", type=Path, default=Path("research/data/liquid_universe"))
    parser.add_argument("--out", type=Path, default=Path("research/data/liquid_replay.json"))
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    parser.add_argument("--trial-registry", type=Path, default=Path("research/trials/registry.jsonl"))
    parser.add_argument("--record-trial", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    started = time.time()
    market, manifest = load_market(args.data_dir)
    params, costs = RadarParams(), ReplayCosts()
    fingerprint = engine_fingerprint()
    report, obs = run_replay(market, params=params, costs=costs, workers=args.workers,
                             fingerprint=fingerprint, progress=True)
    stopped = manifest.get("stopped_before_window_end") or []
    report.notes.append(
        f"Evren: {manifest.get('candidates')} aday çift, {len(stopped)} tanesinin verisi pencere bitmeden "
        "duruyor (kalkan/küçülen coinler dahil)."
    )
    if not stopped:
        report.notes.append("UYARI: verisi erken biten hiç çift yok; survivorship'ten arındırma doğrulanamadı.")
    payload = report.to_dict()
    payload["universe_manifest"] = {k: v for k, v in manifest.items() if k != "symbols"}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_observations(obs, args.out.with_name(args.out.stem + "_observations.csv.gz"))
    tmp = args.out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str), "utf-8")
    tmp.replace(args.out)

    evidence = {k: v for k, v in report.to_dict().items() if k in {"decision", "notes", "engine_fingerprint", "can_authorize_trade"}}
    strategy_report = StrategyReport(
        hypothesis=LIQUID_HYPOTHESIS, fragility_answers=LIQUID_FRAGILITY_ANSWERS,
        blow_up_scenarios=LIQUID_BLOW_UP_SCENARIOS, evidence=evidence,
    )
    args.out.with_suffix(".md").write_text(strategy_report.render_markdown(), "utf-8")

    trial_params = replay_trial_params(fingerprint=fingerprint, params=params, costs=costs)
    trial_id = trial_id_for(family=REPLAY_FAMILY, params=trial_params, dataset={})
    registry = TrialRegistry(args.trial_registry)
    known = {r.trial_id for r in registry.selection_trials(REPLAY_FAMILY)}
    print(f"trial {trial_id} ({'pre-registered' if trial_id in known else 'NOT pre-registered'})"
          f" · family trials: {len(known | {trial_id})}")
    if args.record_trial:
        registry.append(TrialRecord(
            trial_id=trial_id, family=REPLAY_FAMILY, kind="SELECTION_CANDIDATE",
            description="liquid replay CLI run", params=trial_params,
            dataset={"start": report.start_time, "end": report.end_time},
            recorded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            n_trades=sum(g.n for g in report.decision.values()), sharpe_per_trade=None,
        ))
    print_summary(report)
    print(f"wrote {args.out} and {args.out.with_suffix('.md')} in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
