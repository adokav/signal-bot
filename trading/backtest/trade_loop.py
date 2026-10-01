"""Complete entry + exit loops on the point-in-time majors universe. Research only.

Protocol, rules and decisions: docs/TRADE_LOOP_STUDY.md (written before any
result). The user's goal: enter at the right time, exit at the right time,
repeat. Each loop is simulated trade by trade on daily candles built from
closed 15m spot candles:

- decide at the 00:00 UTC daily close with data closed by then;
- trade at the next 15m open;
- a disaster stop is watched intraday on 15m lows (filled at the stop, or at
  the open when the market gaps through it);
- one position per coin; re-enter when the entry condition returns.

Two controls answer the two questions: the same exit rule from 20 random
entry days per trade (is the entry timing worth anything?) and the
equal-weight buy-and-hold basket of the same universe (is the loop worth
anything as a whole?). Token-swap gaps make a trade unknown, never a return
(``weekly_momentum.continuous``). No order authority (AGENTS.md §4, §10).
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from trading.backtest import liquid_replay as lr
from trading.backtest import majors_signals as ms
from trading.backtest import signal_quality as sq
from trading.backtest import weekly_momentum as wm


DAY = 86_400
BARS = lr.DAY_BARS
DISCOVERY = ms.DISCOVERY
CONFIRMATION = ms.CONFIRMATION
DISCOVERY_HALF_AT = ms.DISCOVERY_HALF_AT
HALF_AT = ms.HALF_AT
COST_PCT = ms.COST_PCT
STRESS_COST_PCT = ms.STRESS_COST_PCT
ATR_DAYS = 20
RANDOM_DRAWS = 20
MIN_RANDOM_DRAWS = 10
MIN_CANDIDATE_TRADES = 150
MIN_CONFIRM_TRADES = 60
MAX_UNKNOWN_TRADES = 0.05
MAX_UNKNOWN_DAYS = 0.05


@dataclass(frozen=True)
class Loop:
    name: str
    family: str
    kind: str                   # "channel" or "pullback"
    entry_days: int = 0         # channel: close above the previous N-day high close
    exit_days: int = 0          # channel: close below the previous M-day low close
    exit_rule: str = ""         # pullback: "ema50" or "dip"
    vol_target: float = 0.0     # >0: each trade's slot × min(1, vol_target / 30-day annualised volatility)


LOOPS = (
    Loop("D1_20_10", "D1", "channel", entry_days=20, exit_days=10),
    Loop("D1_55_20", "D1", "channel", entry_days=55, exit_days=20),
    Loop("D2_EMA50", "D2", "pullback", exit_rule="ema50"),
    Loop("D2_DIP", "D2", "pullback", exit_rule="dip"),
)

# Pre-history test (docs/TRADE_LOOP_STUDY.md): the post-discovery hypothesis on 2018-09..2020-09.
D1_VOL = Loop("D1_20_10_VOL", "D1", "channel", entry_days=20, exit_days=10, vol_target=0.50)
PREHISTORY = (1_535_760_000, sq.DISCOVERY_START)     # 2018-09-01 -> 2020-10-01
PREHISTORY_HALF_AT = 1_567_296_000                    # 2019-09-01
PREHISTORY_LOOPS = (LOOPS[0], D1_VOL)
VOL_DAYS = 30
SHARPE_BLOCK_DAYS = 30


# ---------------------------------------------------------------------------
# Daily candles and indicators (closed data only)
# ---------------------------------------------------------------------------


@dataclass
class Daily:
    day0: int                   # 00:00 UTC of day 0 (= the grid start)
    high: np.ndarray            # (symbols, days)
    low: np.ndarray
    close: np.ndarray           # close of the day's last 15m candle; NaN if that candle is missing

    @property
    def n_days(self) -> int:
        return self.close.shape[1]


def daily_bars(market: lr.Market) -> Daily:
    day0 = int(market.grid_open[0])
    if day0 % DAY:
        raise ValueError("the 15m grid must start at 00:00 UTC")
    n_days = market.n_grid // BARS
    shape = (len(market.symbols), n_days, BARS)
    high = market.high[:, : n_days * BARS].reshape(shape)
    low = market.low[:, : n_days * BARS].reshape(shape)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)               # all-NaN days stay NaN
        day_high, day_low = np.nanmax(high, axis=2), np.nanmin(low, axis=2)
    close = market.close[:, : n_days * BARS].reshape(shape)[:, :, -1]
    return Daily(day0, day_high, day_low, close.copy())


def _rolling(values: np.ndarray, days: int, how: str) -> np.ndarray:
    """Statistic of the ``days`` values *before* each day (NaN unless all are known)."""

    import pandas as pd

    frame = pd.DataFrame(values.T)
    rolled = getattr(frame.rolling(days, min_periods=days), how)().shift(1)
    return rolled.to_numpy().T


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    """EMA through each day; NaN unless the last ``span`` closes are all known."""

    import pandas as pd

    frame = pd.DataFrame(values.T)
    ema = frame.ffill().ewm(span=span, adjust=False).mean().to_numpy().T
    known = frame.notna().rolling(span, min_periods=span).sum().to_numpy().T == span
    return np.where(known, ema, np.nan)


@dataclass
class Indicators:
    atr: np.ndarray
    ema20: np.ndarray
    ema50: np.ndarray
    high_close: dict[int, np.ndarray]     # previous N-day highest close
    low_close: dict[int, np.ndarray]      # previous M-day lowest close
    low5: np.ndarray                      # lowest low of the last 5 days, today included
    vol30: np.ndarray                     # annualised std of the last 30 daily log returns, today included


def indicators(daily: Daily) -> Indicators:
    import pandas as pd

    prev_close = np.concatenate([np.full((daily.close.shape[0], 1), np.nan), daily.close[:, :-1]], axis=1)
    tr = np.fmax(daily.high - daily.low, np.fmax(np.abs(daily.high - prev_close), np.abs(daily.low - prev_close)))
    tr = np.where(np.isfinite(daily.high) & np.isfinite(daily.low) & np.isfinite(prev_close), tr, np.nan)
    atr = pd.DataFrame(tr.T).rolling(ATR_DAYS, min_periods=ATR_DAYS).mean().to_numpy().T
    low5 = pd.DataFrame(daily.low.T).rolling(5, min_periods=5).min().to_numpy().T
    with np.errstate(invalid="ignore", divide="ignore"):
        log_ret = np.log(daily.close / prev_close)
    vol30 = pd.DataFrame(log_ret.T).rolling(VOL_DAYS, min_periods=VOL_DAYS).std().to_numpy().T * math.sqrt(365)
    days = {loop.entry_days for loop in LOOPS if loop.entry_days}
    exits = {loop.exit_days for loop in LOOPS if loop.exit_days}
    return Indicators(
        atr=atr, ema20=_ema(daily.close, 20), ema50=_ema(daily.close, 50),
        high_close={n: _rolling(daily.close, n, "max") for n in days},
        low_close={m: _rolling(daily.close, m, "min") for m in exits},
        low5=low5, vol30=vol30,
    )


def entry_signals(loop: Loop, daily: Daily, ind: Indicators) -> np.ndarray:
    """(symbols, days) True where the loop's entry condition holds at that day's close."""

    c = daily.close
    with np.errstate(invalid="ignore"):
        if loop.kind == "channel":
            return c > ind.high_close[loop.entry_days]
        ema20_prev = np.concatenate([np.full((c.shape[0], 1), np.nan), ind.ema20[:, :-1]], axis=1)
        close_prev = np.concatenate([np.full((c.shape[0], 1), np.nan), c[:, :-1]], axis=1)
        ema50_10 = np.concatenate([np.full((c.shape[0], 10), np.nan), ind.ema50[:, :-10]], axis=1)
        trend = (c > ind.ema50) & (ind.ema50 > ema50_10)
        return trend & (c > ind.ema20) & (close_prev <= ema20_prev)


def stop_levels(loop: Loop, ind: Indicators, s: int, d: int, entry_price: float) -> tuple[float, float] | None:
    """(disaster stop, dip level) set at decision day ``d``; None when an input is unknown."""

    atr = ind.atr[s, d]
    if not math.isfinite(atr) or atr <= 0:
        return None
    if loop.kind == "channel":
        return entry_price - 3.0 * atr, math.nan
    dip = ind.low5[s, d]
    if not math.isfinite(dip):
        return None
    return dip - atr, dip


def exit_signal(loop: Loop, daily: Daily, ind: Indicators, s: int, e: int, dip: float) -> bool:
    c = daily.close[s, e]
    if not math.isfinite(c):
        return False                    # no closed candle: no decision that day
    if loop.kind == "channel":
        level = ind.low_close[loop.exit_days][s, e]
    elif loop.exit_rule == "ema50":
        level = ind.ema50[s, e]
    else:
        level = dip
    return math.isfinite(level) and c < level


# ---------------------------------------------------------------------------
# One trade
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Skip:
    """An entry signal that could not become a trade, and why."""

    reason: str                 # window_end / no_close / no_open / break_at_entry / no_atr / gap_below_stop


DATA_SKIPS = frozenset({"no_open", "break_at_entry", "no_atr"})      # missing data, counted as unknown


def halt_bars(market: lr.Market) -> np.ndarray:
    """True for 15m bars where most pairs that traded the day before have no candle: an exchange halt.

    No trade happens during a halt, so no stop can be crossed inside it. A
    pair-specific missing candle is different: the price may have moved
    without us seeing it (``simulate`` then marks the trade unknown).
    """

    import pandas as pd

    counts = np.isfinite(market.close).sum(axis=0).astype(float)
    reference = pd.Series(counts).rolling(BARS, min_periods=1).median().shift(1).to_numpy()
    return np.where(np.isfinite(reference) & (reference > 0), counts < 0.5 * reference, False)


@dataclass(frozen=True)
class Trade:
    symbol: str
    decided_at: int             # the daily close the entry was decided at
    entry_day: int
    entry_price: float
    stop: float
    exit_day: int
    exit_price: float
    exit_reason: str            # signal / stop / delisted / open_at_end
    status: str                 # RESOLVED / UNKNOWN

    @property
    def gross_pct(self) -> float:
        return (self.exit_price / self.entry_price - 1.0) * 100.0

    def net_pct(self, cost: float = COST_PCT) -> float:
        return self.gross_pct - cost

    @property
    def risk_pct(self) -> float:
        return (1.0 - self.stop / self.entry_price) * 100.0

    @property
    def r(self) -> float:
        return self.net_pct() / (self.risk_pct + COST_PCT)


def simulate(market: lr.Market, daily: Daily, ind: Indicators, loop: Loop, s: int, d: int,
             breaks: wm.Breaks, halts: np.ndarray | None = None) -> Trade | Skip:
    """Enter at the open after day ``d``'s close and follow the loop's exit, or say why that was not possible.

    ``halts`` (``halt_bars``) marks exchange-wide halts; without it every
    missing candle inside a trade makes the trade unknown.
    """

    first = (d + 1) * BARS
    if d + 1 >= daily.n_days:
        return Skip("window_end")
    if not math.isfinite(daily.close[s, d]):
        return Skip("no_close")
    if not math.isfinite(market.open[s, first]):
        return Skip("no_open")
    if not wm.continuous(breaks, s, first - 1, first):
        return Skip("break_at_entry")                       # resumes on the entry bar: not tradable at the decision
    entry = float(market.open[s, first])
    levels = stop_levels(loop, ind, s, d, entry)
    if levels is None:
        return Skip("no_atr")
    if levels[0] >= entry:
        return Skip("gap_below_stop")                       # opened at or below its own stop: no entry by rule
    stop, dip = levels
    decided_at = daily.day0 + (d + 1) * DAY
    last = int(market.last_index[s])
    delisted = last < int(market.coverage_end[s]) - BARS

    def done(day: int, price: float, reason: str, bar: int) -> Trade:
        missing = first + np.nonzero(~np.isfinite(market.low[s, first:bar + 1]))[0]
        unseen = len(missing) and (halts is None or not bool(halts[missing].all()))
        ok = wm.continuous(breaks, s, first - 1, bar) and not unseen    # a pair-specific gap could hide the stop
        return Trade(market.symbols[s], decided_at, d + 1, entry, stop, day, float(price), reason,
                     "RESOLVED" if ok else "UNKNOWN")

    for e in range(d + 1, daily.n_days):
        lo, hi = e * BARS, (e + 1) * BARS
        # 1. the disaster stop, intraday, on 15m lows (filled at the stop, or at a gapped open below it)
        if math.isfinite(daily.low[s, e]) and daily.low[s, e] <= stop:
            for b in range(first if e == d + 1 else lo, hi):
                if math.isfinite(market.low[s, b]) and market.low[s, b] <= stop:
                    o = market.open[s, b]
                    return done(e, min(o, stop) if math.isfinite(o) else stop, "stop", b)
        # 2. a pair that stopped trading for good exits at its last close
        if delisted and last < hi:
            return done(e, market.close[s, last], "delisted", last)
        # 3. the loop's exit rule at the daily close, traded at the next available open
        if exit_signal(loop, daily, ind, s, e, dip):
            ahead = np.nonzero(np.isfinite(market.open[s, hi:min(market.n_grid, hi + BARS)]))[0]
            if len(ahead):
                b = hi + int(ahead[0])
                return done(b // BARS, market.open[s, b], "signal", b)
            return done(e, daily.close[s, e], "open_at_end", hi - 1)
    tail = np.nonzero(np.isfinite(daily.close[s, d + 1:]))[0]
    if not len(tail):
        return done(d + 1, entry, "open_at_end", first)
    e = d + 1 + int(tail[-1])
    return done(e, daily.close[s, e], "open_at_end", (e + 1) * BARS - 1)


# ---------------------------------------------------------------------------
# A whole loop: trades, random-entry control, portfolio
# ---------------------------------------------------------------------------


def decision_days(daily: Daily, monthly: Mapping[int, Sequence[str]], window: tuple[int, int],
                  market: lr.Market) -> dict[int, list[tuple[int, int]]]:
    """symbol index -> [(decision day, month start)] for days the symbol is a universe member."""

    starts = sorted(monthly)
    out: dict[int, list[tuple[int, int]]] = {}
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else window[1]
        for symbol in monthly[start]:
            s = market.index_of.get(symbol)
            if s is None:
                continue
            for t in range(max(start, window[0]), min(end, window[1]), DAY):
                d = (t - daily.day0) // DAY - 1
                if 0 <= d < daily.n_days:
                    out.setdefault(s, []).append((d, start))
    return out


def run_loop(market: lr.Market, daily: Daily, ind: Indicators, loop: Loop,
             days: Mapping[int, Sequence[tuple[int, int]]], breaks: wm.Breaks, *, seed: int = 0,
             halts: np.ndarray | None = None) -> tuple[list[dict], dict[str, int]]:
    """Trades of one loop with their random-entry control, and the entry signals that could not trade."""

    signals = entry_signals(loop, daily, ind)
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    skipped: dict[str, int] = {}
    for s in sorted(days):
        by_month: dict[int, list[int]] = {}
        for d, month in days[s]:
            by_month.setdefault(month, []).append(d)
        free_from = -1
        for d, month in days[s]:
            if d < free_from or not signals[s, d]:
                continue
            trade = simulate(market, daily, ind, loop, s, d, breaks, halts)
            if isinstance(trade, Skip):
                skipped[trade.reason] = skipped.get(trade.reason, 0) + 1
                continue
            free_from = trade.exit_day                      # one position per coin; re-enter after the exit
            pool = by_month[month]
            draws = [int(x) for x in rng.choice(pool, size=min(RANDOM_DRAWS, len(pool)), replace=False)]
            random_nets = [t.net_pct() for x in draws
                           if isinstance(t := simulate(market, daily, ind, loop, s, x, breaks, halts), Trade)
                           and t.status == "RESOLVED"]
            rows.append({
                **asdict(trade), "family": loop.family, "loop": loop.name, "month": month,
                "net_pct": trade.net_pct(), "stress_net_pct": trade.net_pct(STRESS_COST_PCT), "r": trade.r,
                "days_held": trade.exit_day - trade.entry_day,
                "random_mean_net_pct": statistics.fmean(random_nets) if len(random_nets) >= MIN_RANDOM_DRAWS else None,
                "random_draws": len(random_nets),
                "vol_30d": float(ind.vol30[s, d]) if math.isfinite(ind.vol30[s, d]) else None,
            })
    return rows, skipped


def daily_path(daily: Daily, row: Mapping[str, Any], s: int) -> dict[int, float]:
    """Day -> simple return of the position on that day (entry and exit days at their fills)."""

    out, prev = {}, float(row["entry_price"])
    for e in range(int(row["entry_day"]), int(row["exit_day"]) + 1):
        if e == int(row["exit_day"]):
            price = float(row["exit_price"])
        else:
            c = daily.close[s, e]
            if not math.isfinite(c):
                out[e] = 0.0
                continue
            price = float(c)
        out[e] = price / prev - 1.0
        prev = price
    return out


def portfolio(market: lr.Market, daily: Daily, rows: Sequence[Mapping[str, Any]],
              monthly: Mapping[int, Sequence[str]], window: tuple[int, int], breaks: wm.Breaks,
              *, half_at: int, vol_target: float = 0.0, alpha: float = 0.05) -> dict[str, Any]:
    """Each trade holds 1/N of capital (N = its month's universe size); vs the equal-weight basket.

    Positions kept after their coin left the universe keep their slot, so a
    day's slots can add up to more than 100%. Such a day is scaled down to
    100% (all positions in proportion): the loop is never levered. With
    ``vol_target`` a slot is cut to 1/N × min(1, vol_target / 30-day volatility
    at the decision); a trade whose volatility is unknown is left out and counted.
    """

    first_day = (window[0] - daily.day0) // DAY
    last_day = min(daily.n_days, (window[1] - daily.day0) // DAY + 1) - 1
    n_days = last_day - first_day + 1
    loop_ret = np.zeros(n_days)
    exposure = np.zeros(n_days)
    unsized = 0
    for row in rows:
        if row["status"] != "RESOLVED":
            continue
        s = market.index_of[row["symbol"]]
        w = 1.0 / max(1, len(monthly[row["month"]]))
        if vol_target:
            vol = row.get("vol_30d")
            if vol is None or not vol > 0:
                unsized += 1
                continue
            w *= min(1.0, vol_target / vol)
        for e, r in daily_path(daily, row, s).items():
            if first_day <= e <= last_day:
                loop_ret[e - first_day] += w * r
                exposure[e - first_day] += w
        for e in (int(row["entry_day"]), int(row["exit_day"])):
            if first_day <= e <= last_day:
                loop_ret[e - first_day] -= w * COST_PCT / 200.0
    starts = sorted(monthly)
    basket = np.full(n_days, np.nan)
    for k in range(n_days):
        e = first_day + k
        t = daily.day0 + e * DAY
        month = max((m for m in starts if m <= t), default=None)
        if month is None or e == 0:
            continue
        rets = []
        for symbol in monthly[month]:
            s = market.index_of.get(symbol)
            if s is None:
                continue
            c0, c1 = daily.close[s, e - 1], daily.close[s, e]
            if math.isfinite(c0) and math.isfinite(c1) and wm.continuous(breaks, s, e * BARS - 1, (e + 1) * BARS - 1):
                rets.append(c1 / c0 - 1.0)
        if rets:
            basket[k] = statistics.fmean(rets)
    scale = np.where(exposure > 1.0, 1.0 / np.maximum(exposure, 1e-12), 1.0)
    loop_ret, capped = loop_ret * scale, exposure > 1.0
    exposure = exposure * scale
    times = daily.day0 + (first_day + np.arange(n_days)) * DAY
    ok = np.isfinite(basket)
    return {
        "loop": _curve(loop_ret[ok], times[ok], half_at),
        "buy_and_hold": _curve(basket[ok], times[ok], half_at),
        "sharpe_difference_ci": sharpe_difference_ci(loop_ret[ok], basket[ok], alpha=alpha),
        "time_in_market": float(exposure[ok].mean()) if ok.any() else None,
        "days": int(ok.sum()),
        "capped_days": int(capped[ok].sum()),
        "unsized_trades": unsized,
    }


def _sharpe(x: np.ndarray) -> np.ndarray:
    sd = x.std(axis=-1, ddof=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(sd > 0, x.mean(axis=-1) / sd * math.sqrt(365), np.nan)


def sharpe_difference_ci(a: np.ndarray, b: np.ndarray, *, alpha: float, block: int = SHARPE_BLOCK_DAYS,
                         n_resamples: int = 2000, seed: int = 0) -> tuple[float, float] | None:
    """CI of Sharpe(a) − Sharpe(b) on paired daily returns, circular blocks of ``block`` days (information only)."""

    n = len(a)
    if n < 4 * block:
        return None
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(n_resamples, -(-n // block)))
    idx = ((starts[:, :, None] + np.arange(block)) % n).reshape(n_resamples, -1)[:, :n]
    diff = _sharpe(a[idx]) - _sharpe(b[idx])
    diff = diff[np.isfinite(diff)]
    if len(diff) < 100:
        return None
    return float(np.quantile(diff, alpha / 2)), float(np.quantile(diff, 1 - alpha / 2))


def _curve(returns: np.ndarray, times: np.ndarray, half_at: int) -> dict[str, Any]:
    def sharpe(x: np.ndarray) -> float | None:
        return float(x.mean() / x.std(ddof=1) * math.sqrt(365)) if len(x) > 2 and x.std(ddof=1) > 0 else None

    equity = np.cumprod(1.0 + returns) if len(returns) else np.array([1.0])
    peak = np.maximum.accumulate(equity)
    return {
        "total_return_pct": float((equity[-1] - 1.0) * 100.0),
        "max_drawdown_pct": float((equity / peak - 1.0).min() * 100.0),
        "sharpe": sharpe(returns),
        "sharpe_h1": sharpe(returns[times < half_at]),
        "sharpe_h2": sharpe(returns[times >= half_at]),
    }


# ---------------------------------------------------------------------------
# Statistics and decisions (docs/TRADE_LOOP_STUDY.md)
# ---------------------------------------------------------------------------


def _pairs(rows: Iterable[Mapping[str, Any]], value) -> sq.Pairs:
    out = []
    for row in rows:
        v = value(row)
        if v is not None and math.isfinite(v):
            out.append((float(v), int(row["decided_at"])))
    return out


def evaluate(rows: Sequence[Mapping[str, Any]], book: Mapping[str, Any], *, alpha: float,
             half_at: int) -> dict[str, Any]:
    resolved = [r for r in rows if r["status"] == "RESOLVED"]
    paired = [r for r in resolved if r["random_mean_net_pct"] is not None]
    return {
        "trades": len(rows),
        "resolved": len(resolved),
        "unknown": len(rows) - len(resolved),
        "open_at_end": sum(r["exit_reason"] == "open_at_end" for r in resolved),
        "stops": sum(r["exit_reason"] == "stop" for r in resolved),
        "median_days_held": statistics.median([r["days_held"] for r in resolved]) if resolved else None,
        "win_rate": (sum(r["net_pct"] > 0 for r in resolved) / len(resolved)) if resolved else None,
        "best_pct": max((r["net_pct"] for r in resolved), default=None),
        "worst_pct": min((r["net_pct"] for r in resolved), default=None),
        "net_pct": ms.summarize(_pairs(resolved, lambda r: r["net_pct"]), alpha=alpha, half_at=half_at),
        "stress_net_pct": ms.summarize(_pairs(resolved, lambda r: r["stress_net_pct"]), alpha=alpha,
                                       half_at=half_at),
        "r": ms.summarize(_pairs(resolved, lambda r: r["r"]), alpha=alpha, half_at=half_at),
        "minus_random_pct": ms.summarize(_pairs(paired, lambda r: r["net_pct"] - r["random_mean_net_pct"]),
                                         alpha=alpha, half_at=half_at),
        "random_net_pct": ms.summarize(_pairs(paired, lambda r: r["random_mean_net_pct"]), alpha=alpha,
                                       half_at=half_at),
        "portfolio": book,
    }


def _sharpe_ok(book: Mapping[str, Any], key: str = "sharpe") -> bool:
    mine, base = book["loop"].get(key), book["buy_and_hold"].get(key)
    return mine is not None and base is not None and mine >= base


def is_candidate(result: Mapping[str, Any]) -> bool:
    net, diff = result["net_pct"], result["minus_random_pct"]
    return (result["resolved"] >= MIN_CANDIDATE_TRADES and ms._all_positive(net) and ms._all_positive(diff)
            and _sharpe_ok(result["portfolio"]))


def select_candidates(results: Sequence[Mapping[str, Any]], limit: int = 2) -> list[dict[str, Any]]:
    eligible = sorted((r for r in results if is_candidate(r)), key=lambda r: -r["minus_random_pct"]["ci"][0])
    chosen, families = [], set()
    for r in eligible:
        if r["family"] in families:
            continue
        families.add(r["family"])
        chosen.append({"loop": r["loop"], "family": r["family"], "score": r["minus_random_pct"]["ci"][0]})
        if len(chosen) == limit:
            break
    return chosen


def completeness(rows: Sequence[Mapping[str, Any]], daily: Daily, market: lr.Market,
                 days: Mapping[int, Sequence[tuple[int, int]]], monthly: Mapping[int, Sequence[str]],
                 skipped: Mapping[str, int] | None = None) -> dict:
    coin_days = [(s, d) for s, items in days.items() for d, _ in items]
    unknown_days = sum(not math.isfinite(daily.close[s, d]) for s, d in coin_days)
    data_skips = sum(n for reason, n in (skipped or {}).items() if reason in DATA_SKIPS)
    attempts = len(rows) + data_skips
    return {
        "trades": len(rows),
        "skipped_entries": dict(skipped or {}),
        "unknown_trade_share": ((sum(r["status"] != "RESOLVED" for r in rows) + data_skips) / attempts
                                if attempts else 1.0),
        "unknown_day_share": unknown_days / len(coin_days) if coin_days else 1.0,
        "months_missing_fixed": sum(not set(ms.FIXED) <= set(m) for m in monthly.values()),
    }


def incomplete(stats: Mapping[str, Any]) -> list[str]:
    problems = []
    if not stats["trades"]:
        problems.append("no trades")
    if stats["unknown_trade_share"] > MAX_UNKNOWN_TRADES:
        problems.append(f"unknown trades {stats['unknown_trade_share']:.1%}")
    if stats["unknown_day_share"] > MAX_UNKNOWN_DAYS:
        problems.append(f"unknown daily candles {stats['unknown_day_share']:.1%}")
    if stats["months_missing_fixed"]:
        problems.append(f"{stats['months_missing_fixed']} months without BTC and ETH")
    return problems


def verdict(result: Mapping[str, Any], problems: Sequence[str]) -> str:
    if problems:
        return "INCOMPLETE_DATA"
    net, diff, book = result["net_pct"], result["minus_random_pct"], result["portfolio"]
    if (result["resolved"] >= MIN_CONFIRM_TRADES and ms._all_positive(net) and ms._all_positive(diff)
            and (result["stress_net_pct"]["mean"] or 0) > 0 and _sharpe_ok(book)):
        return "PASS"
    if net.get("ci") and net["ci"][0] > 0:
        return "ZAMANLAMA_YOK"
    smaller_dd = book["loop"]["max_drawdown_pct"] >= 0.5 * book["buy_and_hold"]["max_drawdown_pct"]
    if _sharpe_ok(book) and smaller_dd and _sharpe_ok(book, "sharpe_h1") and _sharpe_ok(book, "sharpe_h2"):
        return "RISK_AZALTIR"
    return "NO_EFFECT"


# ---------------------------------------------------------------------------
# Pre-registration
# ---------------------------------------------------------------------------


FAMILY = "trade_loop_majors"
FINGERPRINT_FILES = ("trading/backtest/trade_loop.py", *ms.FINGERPRINT_FILES)
REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = {"source": "binance_vision_spot", "built_as_of": sq.CONFIRMATION_END,
           "confirmation_window": "2024-09..2026-08"}

# Filled in only after discovery, by the rule in docs/TRADE_LOOP_STUDY.md (loop names).
REGISTERED: tuple[str, ...] = ()


def code_fingerprint(repo_root: Path = REPO_ROOT) -> str:
    digest = hashlib.sha256()
    for name in FINGERPRINT_FILES:
        digest.update(name.encode())
        digest.update((repo_root / name).read_bytes())
    return digest.hexdigest()[:16]


def trial_params() -> dict[str, Any]:
    return {
        "code_fingerprint": code_fingerprint(),
        "loops": list(REGISTERED),
        "universe": "majors_signals monthly universe (BTC, ETH, top 10 by 30d volume, largest meme)",
        "execution": "decide at the 00:00 UTC daily close, trade at the next 15m open, intraday disaster stop",
        "costs_pct": {"base": COST_PCT, "stress": STRESS_COST_PCT},
        "random_control": {"draws": RANDOM_DRAWS, "min_draws": MIN_RANDOM_DRAWS, "same_month": True, "seed": 0},
        "window": list(CONFIRMATION),
        "half_at": HALF_AT,
        "family_alpha": 0.05 / max(1, len(REGISTERED)),
        "bootstrap": {"method": "circular_moving_block_days", "block_days": sq.BLOCK_DAYS, "resamples": 4000},
        "min_trades": MIN_CONFIRM_TRADES,
        "completeness": {"max_unknown_trades": MAX_UNKNOWN_TRADES, "max_unknown_days": MAX_UNKNOWN_DAYS},
        "verdicts": "PASS / ZAMANLAMA_YOK / RISK_AZALTIR / NO_EFFECT; INCOMPLETE_DATA gives no verdict",
    }


def trial_id() -> str:
    from trading.research.robustness import trial_id_for

    return trial_id_for(family=FAMILY, params=trial_params(), dataset=DATASET)


PREHISTORY_FAMILY = "trade_loop_prehistory"
PREHISTORY_DATASET = {"source": "binance_vision_spot", "built_as_of": sq.DISCOVERY_START,
                      "window": "2018-09..2020-09"}


def prehistory_trial_params() -> dict[str, Any]:
    return {
        "code_fingerprint": code_fingerprint(),
        "loops": [loop.name for loop in PREHISTORY_LOOPS],
        "vol_target": D1_VOL.vol_target,
        "chosen_after_discovery": True,
        "universe": "majors_signals monthly universe without the perpetual condition (none before 2019-09)",
        "execution": "decide at the 00:00 UTC daily close, trade at the next 15m open, intraday disaster stop",
        "costs_pct": {"base": COST_PCT, "stress": STRESS_COST_PCT},
        "random_control": {"draws": RANDOM_DRAWS, "min_draws": MIN_RANDOM_DRAWS, "same_month": True, "seed": 0},
        "window": list(PREHISTORY),
        "half_at": PREHISTORY_HALF_AT,
        "family_alpha": 0.05 / len(PREHISTORY_LOOPS),
        "bootstrap": {"trades": {"method": "circular_moving_block_days", "block_days": sq.BLOCK_DAYS},
                      "sharpe_difference": {"block_days": SHARPE_BLOCK_DAYS, "information_only": True}},
        "min_trades": MIN_CONFIRM_TRADES,
        "verdicts": "PASS / ZAMANLAMA_YOK / RISK_AZALTIR / NO_EFFECT; INCOMPLETE_DATA gives no verdict",
    }


def prehistory_trial_id() -> str:
    from trading.research.robustness import trial_id_for

    return trial_id_for(family=PREHISTORY_FAMILY, params=prehistory_trial_params(), dataset=PREHISTORY_DATASET)


def require_prehistory_registration(registry: Path) -> str:
    from trading.research.robustness import TrialRegistry

    tid = prehistory_trial_id()
    if tid not in {r.trial_id for r in TrialRegistry(registry).selection_trials(PREHISTORY_FAMILY)}:
        raise SystemExit(f"pre-history trial {tid} is not pre-registered for this code; refusing to run it")
    return tid


def require_registration(registry: Path) -> str:
    from trading.research.robustness import TrialRegistry

    if not REGISTERED:
        raise SystemExit("no loop is registered; the confirmation window stays sealed")
    tid = trial_id()
    if tid not in {r.trial_id for r in TrialRegistry(registry).selection_trials(FAMILY)}:
        raise SystemExit(f"trial {tid} is not pre-registered for this code; refusing to look at 2024-09+")
    return tid


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run(spot_dir: Path, *, window: tuple[int, int], end: int, alpha: float, half_at: int,
        loops: Sequence[Loop] = LOOPS) -> dict[str, Any]:
    market, excluded, _, perp_of, funding_times = ms.load_inputs(spot_dir, end=end)
    monthly = ms.universes(market, window, perp_of=perp_of, funding_times=funding_times)
    return evaluate_loops(market, excluded, monthly, window=window, alpha=alpha, half_at=half_at, loops=loops)


def run_prehistory(spot_dir: Path, *, alpha: float) -> dict[str, Any]:
    """The pre-registered pre-history test: spot-only majors universe (no perpetuals before 2019-09)."""

    market, _, excluded = sq.load_likit_market(spot_dir, end=PREHISTORY[1])
    monthly = ms.universes(market, PREHISTORY, perp_of={}, funding_times={}, require_perp=False)
    return evaluate_loops(market, excluded, monthly, window=PREHISTORY, alpha=alpha, half_at=PREHISTORY_HALF_AT,
                          loops=PREHISTORY_LOOPS)


def evaluate_loops(market: lr.Market, excluded: Mapping[str, str], monthly: Mapping[int, Sequence[str]], *,
                   window: tuple[int, int], alpha: float, half_at: int, loops: Sequence[Loop]) -> dict[str, Any]:
    daily = daily_bars(market)
    ind = indicators(daily)
    breaks, halts = wm.continuity_breaks(market), halt_bars(market)
    days = decision_days(daily, monthly, window, market)
    results, all_rows = [], []
    for loop in loops:
        rows, skipped = run_loop(market, daily, ind, loop, days, breaks, halts=halts)
        book = portfolio(market, daily, rows, monthly, window, breaks, half_at=half_at,
                         vol_target=loop.vol_target, alpha=alpha)
        stats = completeness(rows, daily, market, days, monthly, skipped)
        result = {"loop": loop.name, "family": loop.family, **evaluate(rows, book, alpha=alpha, half_at=half_at),
                  "completeness": stats, "incomplete": incomplete(stats)}
        results.append(result)
        all_rows += rows
    return {"excluded": excluded, "monthly": {str(k): v for k, v in monthly.items()}, "results": results,
            "rows": all_rows}


def _fmt(value: Any, spec: str = "+.3f") -> str:
    return "n/a" if value is None else format(value, spec)


def _line(name: str, s: Mapping[str, Any]) -> str:
    ci = "n/a" if not s.get("ci") else f"[{s['ci'][0]:+.3f},{s['ci'][1]:+.3f}]"
    return f"    {name:<18} n={s['n']:>5} mean={_fmt(s['mean'])} ci={ci} h1={_fmt(s['h1'])} h2={_fmt(s['h2'])}"


def print_report(report: Mapping[str, Any]) -> None:
    for r in report["results"]:
        book = r["portfolio"]
        print(f"## {r['loop']}: trades {r['trades']} (resolved {r['resolved']}, unknown {r['unknown']}, "
              f"open at end {r['open_at_end']}, stops {r['stops']}), median days {r['median_days_held']}, "
              f"win {_fmt(r['win_rate'], '.0%')}, best {_fmt(r['best_pct'], '+.1f')}, worst {_fmt(r['worst_pct'], '+.1f')}"
              + (f"  incomplete={r['incomplete']}" if r["incomplete"] else "")
              + (f"  -> {r['verdict']}" if "verdict" in r else ""))
        for key in ("net_pct", "stress_net_pct", "r", "random_net_pct", "minus_random_pct"):
            print(_line(key, r[key]))
        for side in ("loop", "buy_and_hold"):
            c = book[side]
            print(f"    {side:<18} total {c['total_return_pct']:+.1f}%  maxDD {c['max_drawdown_pct']:+.1f}%  "
                  f"sharpe {_fmt(c['sharpe'], '.2f')} (h1 {_fmt(c['sharpe_h1'], '.2f')}, h2 {_fmt(c['sharpe_h2'], '.2f')})")
        ci = book.get("sharpe_difference_ci")
        print(f"    sharpe difference (loop - buy_and_hold) ci "
              + ("n/a" if not ci else f"[{ci[0]:+.2f},{ci[1]:+.2f}]") + " (information only)")
        print(f"    time in market {_fmt(book['time_in_market'], '.0%')} over {book['days']} days, "
              f"exposure capped at 100% on {book['capped_days']} days, unsized trades {book['unsized_trades']}; "
              f"skipped entries {json.dumps(r['completeness']['skipped_entries'], sort_keys=True)}")


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse
    import logging

    parser = argparse.ArgumentParser(description="Trade loop study (docs/TRADE_LOOP_STUDY.md).")
    sub = parser.add_subparsers(dest="mode", required=True)
    disc = sub.add_parser("discover", help="all four loops on the sealed discovery window")
    build = sub.add_parser("build-confirm", help="confirmation data as of 2026-09-01 (registered trial only)")
    conf = sub.add_parser("confirm", help="the registered loops, once, on 2024-09..2026-08")
    pre = sub.add_parser("prehistory", help="the registered pre-history test, once, on 2018-09..2020-09")
    for p in (disc, conf, pre):
        p.add_argument("--spot-dir", type=Path, required=True)
        p.add_argument("--out", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    for p in (build, conf, pre):
        p.add_argument("--trial-registry", type=Path, default=Path("research/trials/registry.jsonl"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    started = time.time()
    if args.mode == "build-confirm":
        from trading.data.binance_universe import build_universe
        from trading.data.universe_funding import build_funding

        trial = require_registration(args.trial_registry)
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        build_universe(args.out, lookback_months=sq.CONFIRMATION_MONTHS, decision_at=sq.CONFIRMATION_END)
        build_funding(args.out, args.out / "funding", decision_at=sq.CONFIRMATION_END)
        print(json.dumps({"trial": trial, "built": str(args.out)}))
        return 0
    if args.mode == "discover":
        trial = None
        report = run(args.spot_dir, window=DISCOVERY, end=sq.DISCOVERY_END, alpha=0.05, half_at=DISCOVERY_HALF_AT)
        report["candidates"] = select_candidates([r for r in report["results"] if not r["incomplete"]])
    elif args.mode == "prehistory":
        trial = require_prehistory_registration(args.trial_registry)
        report = run_prehistory(args.spot_dir, alpha=0.05 / len(PREHISTORY_LOOPS))
        for r in report["results"]:
            r["verdict"] = verdict(r, r["incomplete"])
    else:
        trial = require_registration(args.trial_registry)
        loops = [loop for loop in LOOPS if loop.name in REGISTERED]
        report = run(args.spot_dir, window=CONFIRMATION, end=sq.CONFIRMATION_END, alpha=0.05 / len(loops),
                     half_at=HALF_AT, loops=loops)
        for r in report["results"]:
            r["verdict"] = verdict(r, r["incomplete"])
    rows = report.pop("rows")
    print(f"excluded (history identity): {json.dumps(report['excluded'], sort_keys=True)}")
    print(f"{args.mode}: {len(report['monthly'])} months, {len(rows)} trades"
          + (f", trial {trial} (pre-registered)" if trial else ""))
    print_report(report)
    if "candidates" in report:
        print("candidates (rule in docs/TRADE_LOOP_STUDY.md):", json.dumps(report["candidates"]))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_name(args.out.name + ".tmp")
    tmp.write_text(json.dumps({"mode": args.mode, "trial": trial, **report, "trades": rows,
                               "can_authorize_trade": False}, indent=1, default=str), "utf-8")
    tmp.replace(args.out)
    print(f"wrote {args.out} in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
