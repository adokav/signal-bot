"""Shared point-in-time market matrices, study windows and block-bootstrap helpers. Research only.

These pieces were moved unchanged from the closed-study harnesses removed in
dead-code wave 3 (``liquid_replay``, ``signal_quality``, ``weekly_momentum``;
docs/DEAD_CODE_AUDIT.md) because ``trade_loop``, ``majors_signals`` and
``atlas`` still use them. Nothing here selects, scores or ranks a coin and
nothing can place or authorize an order (AGENTS.md §4, §10).

- **Market matrices:** per-symbol 15m Binance spot columns aligned on one
  grid, malformed rows dropped (never repaired), trailing 24h volume and
  change, the last bar with data and the last bar the download covered.
- **Forward returns:** next open to the close ``bars`` later; a pair whose
  data stops well before its coverage ends exits at the last traded close
  (delisting), anything else unknowable stays NaN.
- **Study windows:** the sealed discovery / confirmation boundaries shared by
  every study on the Binance spot universe.
- **Block bootstrap:** circular moving blocks over calendar days, the same
  blocks for every group, so dependence within a block is kept.
- **Continuity:** bars where a symbol's data resumes after a hole longer
  than a day; a token swap under the same symbol is a break, not a return.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from trading.data.binance_universe import coverage_end

BAR_SECONDS = 900
DAY_BARS = 96

# Sealed study windows on the Binance spot universe (docs/SIGNAL_QUALITY.md, docs/TRADE_LOOP_STUDY.md).
DISCOVERY_START = 1_601_510_400   # 2020-10-01T00:00:00Z
DISCOVERY_END = 1_725_148_800     # 2024-09-01T00:00:00Z, exclusive
CONFIRMATION_END = 1_788_220_800  # 2026-09-01T00:00:00Z, exclusive
DISCOVERY_MONTHS = 47             # 2020-10 .. 2024-08
CONFIRMATION_MONTHS = 27          # 2024-06 .. 2026-08: warm-up before 2024-09, then the window
HALF_AT = 1_756_684_800           # 2025-09-01T00:00:00Z: fixed split of the confirmation window
BLOCK_DAYS = 7
FILTER_ALPHA = 0.05 / 2           # the signal-quality family's alpha; kept as the default of diff_ci


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
    coverage = np.full(len(symbols), -1, dtype=np.int64)
    for s, symbol in enumerate(symbols):
        cols = cleaned.pop(symbol)
        idx = (cols["open_time"] - start) // BAR_SECONDS
        for name in names:
            mats[name][s, idx] = cols[name]
        last_index[s] = idx[-1]
        cov = (coverage_end_time or {}).get(symbol)
        coverage[s] = idx[-1] if cov is None else min(len(grid_open) - 1, (int(cov) - start) // BAR_SECONDS)
    qv24, chg24 = _rolling_24h(mats["quote_volume"], mats["close"])
    return Market(symbols, grid_open, mats["open"], mats["high"], mats["low"], mats["close"],
                  mats["quote_volume"], qv24, chg24, last_index, coverage, rejected)


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


class SealError(RuntimeError):
    """The dataset contains candles from a window that must stay unseen."""


def check_sealed(last_close: int, *, end: int) -> None:
    if last_close >= end:
        raise SealError(f"data closes at {last_close}, after the sealed boundary {end}")


def load_likit_market(data_dir: Path, *, end: int) -> tuple[Market, dict, dict[str, str]]:
    """``load_market`` minus pairs that are not ordinary coins in Binance history.

    The live identity rules miss some historical pairs (PAX, UST, AUD, BULL/BEAR,
    wrapped coins); the history identity layer removes them before anything is
    ranked, so they can be neither a signal nor part of the benchmark.
    """

    import pandas as pd

    from trading.data.binance_history_identity import exclusion_reason

    manifest = json.loads((data_dir / "manifest.json").read_text("utf-8"))
    quote = "USDT"
    bases = {symbol[: -len(quote)] for symbol in manifest["symbols"]}
    excluded: dict[str, str] = {}
    columns: dict[str, dict[str, np.ndarray]] = {}
    coverage: dict[str, int] = {}
    for symbol, info in manifest["symbols"].items():
        reason = exclusion_reason(symbol[: -len(quote)], bases)
        if reason is not None:
            excluded[symbol] = reason
            continue
        frame = pd.read_parquet(data_dir / "15m" / f"{symbol}.parquet", columns=list(COLUMNS))
        columns[symbol] = {name: frame[name].to_numpy() for name in COLUMNS}
        year, month = (int(x) for x in info["months"][-1].split("-"))
        coverage[symbol] = coverage_end((year, month)) - (BAR_SECONDS - 1)
    market = build_market(columns, coverage_end_time=coverage)
    check_sealed(int(market.grid_open[-1]) + BAR_SECONDS - 1, end=end)
    return market, manifest, excluded


# ---------------------------------------------------------------------------
# Forward returns
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Continuity: a hole longer than a day is a break (token swaps, halts), never a return
# ---------------------------------------------------------------------------


Breaks = Mapping[int, np.ndarray]


def continuity_breaks(market: Market) -> dict[int, np.ndarray]:
    """Per symbol, the sorted bars where data resumes after a hole longer than a day."""

    out = {}
    for s in range(len(market.symbols)):
        idx = np.nonzero(np.isfinite(market.close[s]))[0]
        resumed = idx[1:][np.diff(idx) - 1 > DAY_BARS]
        if len(resumed):
            out[s] = resumed
    return out


def continuous(breaks: Breaks, s: int, a: int, b: int) -> bool:
    """True when no break lies in the bars (a, b]."""

    resumed = breaks.get(s)
    if resumed is None:
        return True
    k = int(np.searchsorted(resumed, a, side="right"))
    return k >= len(resumed) or int(resumed[k]) > b


# ---------------------------------------------------------------------------
# Block bootstrap over calendar days
# ---------------------------------------------------------------------------


Pairs = list[tuple[float, int]]


def _pairs(rows: Iterable[Mapping[str, Any]], value: Callable[[Mapping[str, Any]], float | None]) -> Pairs:
    out = []
    for row in rows:
        v = value(row)
        if v is not None and math.isfinite(v):
            out.append((float(v), int(row["decided_at"])))
    return out


def block_bootstrap_means(groups: Sequence[Pairs], *, block_days: int = BLOCK_DAYS, n_resamples: int = 4000,
                          seed: int = 0) -> np.ndarray:
    """Circular moving-block bootstrap over calendar days; returns (n_resamples, len(groups)) means.

    Every group is resampled with the same blocks of ``block_days`` consecutive
    UTC days (empty days included), so dependence across days within a block
    (overlapping 72h outcomes, shared market moves) is kept, not assumed away.
    """

    first = min(t // 86_400 for pairs in groups for _, t in pairs)
    last = max(t // 86_400 for pairs in groups for _, t in pairs)
    days = last - first + 1
    sums, counts = [], []
    for pairs in groups:
        labels = np.array([t // 86_400 - first for _, t in pairs], dtype=np.int64)
        sums.append(np.bincount(labels, weights=[v for v, _ in pairs], minlength=days))
        counts.append(np.bincount(labels, minlength=days).astype(float))
    block = max(1, min(block_days, days))
    n_blocks = -(-days // block)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, days, size=(n_resamples, n_blocks))
    idx = ((starts[:, :, None] + np.arange(block)) % days).reshape(n_resamples, -1)[:, :days]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.stack([s[idx].sum(1) / c[idx].sum(1) for s, c in zip(sums, counts)], axis=1)


def _interval(draws: np.ndarray, alpha: float) -> tuple[float, float] | None:
    draws = draws[np.isfinite(draws)]
    if len(draws) < 100:
        return None
    return float(np.quantile(draws, alpha / 2)), float(np.quantile(draws, 1 - alpha / 2))


def diff_ci(a: Pairs, b: Pairs, *, alpha: float = FILTER_ALPHA, n_resamples: int = 4000,
            seed: int = 0) -> tuple[float, float] | None:
    """CI of mean(a) − mean(b), both groups resampled with the same day blocks."""

    if len(a) < 2 or len(b) < 2:
        return None
    means = block_bootstrap_means([a, b], n_resamples=n_resamples, seed=seed)
    return _interval(means[:, 0] - means[:, 1], alpha)
