"""Signal-quality feature table: what each long signal looked like, and what followed. Research only.

Step 1 of the signal-quality plan (docs/SIGNAL_QUALITY.md). For every long
signal the live bot would have sent, Likit-100 top-3 alerts and BTC/ETH
tactical alerts, this records the market at the alert (features) and what
happened next (outcomes). The table is the input for choosing at most three
filters to pre-register. It decides nothing by itself.

Rules:

- **Production code, unchanged.** Signals come from phase 1 of
  ``liquid_replay`` and ``tactical_replay``. The alert rule
  (``long_alerts.can_open``), the stop plan (``compute_stop_plan`` /
  ``plan_from_levels``) and the stop tracking (``track_entry``) are the live
  functions. Files that fingerprint other trials are imported, not modified.
- **Sealed windows.** Discovery is 2020-10 → 2024-08. Its data is built as of
  ``DISCOVERY_END``, and the CLI refuses any dataset with a candle that closes
  later. The confirmation window (2024-09 → 2026-08) is evaluated once, after
  the filters are pre-registered.
- **Point in time.** Features use only candles closed at the alert. Outcomes
  start with the next candle.
- **Missing stays missing.** An unavailable feature is ``None``, never a
  neutral number (AGENTS.md §2).

No order authority anywhere (AGENTS.md §4, §10).
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import inspect
import json
import math
import os
import statistics
import time
from bisect import bisect_left, bisect_right
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from acce_unified import long_alerts
from acce_unified.liquid_long import score_technical_long
from acce_unified.tactical_long_data import TACTICAL_SYMBOLS, TacticalTimeframe
from trading.backtest import liquid_replay as lr
from trading.backtest import tactical_replay as tr


DISCOVERY_START = 1_601_510_400   # 2020-10-01T00:00:00Z
DISCOVERY_END = 1_725_148_800     # 2024-09-01T00:00:00Z, exclusive
CONFIRMATION_END = 1_788_220_800  # 2026-09-01T00:00:00Z, exclusive
DISCOVERY_MONTHS = 47             # 2020-10 .. 2024-08
HORIZONS_H = (4, 24, 72)
BAR = lr.BAR_SECONDS
DAY_BARS = lr.DAY_BARS
TRACK_BARS = long_alerts.TRACK_HOURS * 3_600 // BAR
HOURLY_ROWS = 40                  # the live stop plan requests 40 1h klines
FUNDING_MAX_AGE = 12 * 3_600      # 8h settlements; older than this is stale
BASIS_RATIO_BOUNDS = (0.8, 1.25)  # outside: the perpetual is not the same asset/unit as the spot pair
BASIS_MIN_HOURS = 20              # of the last 24 for the 24h average
LIKIT_COST_PCT = lr.ReplayCosts().round_trip_pct()
TACTICAL_COST_PCT = tr.ReplayCosts().round_trip_pct()


class SealError(RuntimeError):
    """The dataset contains candles from a window that must stay unseen."""


def check_sealed(last_close: int, *, end: int) -> None:
    if last_close >= end:
        raise SealError(f"data closes at {last_close}, after the sealed boundary {end}")


def _f(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


# ---------------------------------------------------------------------------
# Likit-100: features
# ---------------------------------------------------------------------------


def _change(market: lr.Market, s: int, i: int, bars: int) -> float | None:
    if i - bars < 0:
        return None
    a, b = market.close[s, i - bars], market.close[s, i]
    return float((b / a - 1.0) * 100.0) if np.isfinite(a) and np.isfinite(b) else None


def _sma_distance(market: lr.Market, members: np.ndarray, i: int, days: int = 20) -> np.ndarray:
    """% distance of the close from its 20-day average (closes sampled 24h apart); NaN unless all exist."""

    idx = i - DAY_BARS * np.arange(days)
    if idx[-1] < 0:
        return np.full(len(members), np.nan)
    closes = market.close[np.ix_(members, idx)]
    with np.errstate(invalid="ignore", divide="ignore"):
        return (closes[:, 0] / closes.mean(axis=1) - 1.0) * 100.0


def market_features(market: lr.Market, step: lr.Step, i: int) -> dict[str, Any]:
    """BTC and equal-weight universe state at the close of bar ``i``."""

    out: dict[str, Any] = {"regime": step.regime}
    btc = market.index_of.get("BTCUSDT")
    for name, bars in (("btc_24h", DAY_BARS), ("btc_7d", 7 * DAY_BARS), ("btc_30d", 30 * DAY_BARS)):
        out[name] = None if btc is None else _change(market, btc, i, bars)
    out["btc_sma20d"] = None if btc is None else _f(_sma_distance(market, np.array([btc]), i)[0])
    members = np.asarray(step.universe, dtype=np.int64)
    chg24 = market.chg24[members, i]
    chg24 = chg24[np.isfinite(chg24)]
    out["basket_24h"] = float(np.median(chg24)) if len(chg24) >= 50 else None
    if i - 7 * DAY_BARS >= 0:
        with np.errstate(invalid="ignore", divide="ignore"):
            r7 = (market.close[members, i] / market.close[members, i - 7 * DAY_BARS] - 1.0) * 100.0
        r7 = r7[np.isfinite(r7)]
        out["basket_7d"] = float(np.median(r7)) if len(r7) >= 50 else None
    else:
        out["basket_7d"] = None
    dist = _sma_distance(market, members, i)
    dist = dist[np.isfinite(dist)]
    out["breadth_sma20d"] = float((dist > 0).mean() * 100.0) if len(dist) >= 50 else None
    return out


def funding_at(funding: Mapping[str, tuple[list[int], list[float]]] | None, symbol: str, t: int) -> dict[str, Any]:
    """Last settled funding rate and its 3-day mean (% per settlement) visible at ``t``."""

    if funding is None:
        return {"funding_status": "NOT_LOADED", "funding_last": None, "funding_3d": None}
    series = funding.get(symbol)
    if not series:
        return {"funding_status": "NO_PERP", "funding_last": None, "funding_3d": None}
    times, rates = series
    k = bisect_right(times, t)
    if k == 0 or t - times[k - 1] > FUNDING_MAX_AGE:
        return {"funding_status": "STALE", "funding_last": None, "funding_3d": None}
    j = bisect_right(times, t - 3 * 86_400)
    return {
        "funding_status": "OK",
        "funding_last": rates[k - 1] * 100.0,
        "funding_3d": statistics.fmean(rates[j:k]) * 100.0,
    }


def _basis_summary(ratios: Sequence[tuple[int, float]]) -> dict[str, Any]:
    """Basis (%) from (hours ago, perp/spot) pairs; the newest must be the last closed hour."""

    if not ratios or ratios[0][0] != 1:
        return {"basis_status": "STALE", "basis_pct": None, "basis_24h": None}
    lo, hi = BASIS_RATIO_BOUNDS
    if not lo <= ratios[0][1] <= hi:
        return {"basis_status": "MISMATCH", "basis_pct": None, "basis_24h": None}
    usable = [r for _, r in ratios if lo <= r <= hi]
    return {
        "basis_status": "OK",
        "basis_pct": (ratios[0][1] - 1.0) * 100.0,
        "basis_24h": (statistics.fmean(usable) - 1.0) * 100.0 if len(usable) >= BASIS_MIN_HOURS else None,
    }


def _perp_close(series: tuple[np.ndarray, np.ndarray], hour_open: int) -> float | None:
    times, closes = series
    j = int(np.searchsorted(times, hour_open))
    return float(closes[j]) if j < len(times) and int(times[j]) == hour_open else None


def basis_at(perp: Mapping[str, tuple[np.ndarray, np.ndarray]] | None, market: lr.Market, s: int,
             i: int) -> dict[str, Any]:
    """Perpetual close / spot close − 1 for the last closed hour (and its 24h mean), visible after bar ``i``.

    Both sides use the same hour, closed at the alert. No direction is assumed:
    a high basis may mean live demand or a crowded leveraged long (AGENTS.md §6).
    """

    if perp is None:
        return {"basis_status": "NOT_LOADED", "basis_pct": None, "basis_24h": None}
    series = perp.get(market.symbols[s])
    if series is None or not len(series[0]):
        return {"basis_status": "NO_PERP", "basis_pct": None, "basis_24h": None}
    now = int(market.grid_open[i]) + BAR
    last_end = now - now % 3_600
    g0 = int(market.grid_open[0])
    ratios = []
    for h in range(1, 25):
        hour = last_end - h * 3_600
        k = (hour + 3_600 - BAR - g0) // BAR      # the hour's last 15m bar
        if k < 0 or k > i:
            continue
        spot = market.close[s, k]
        close = _perp_close(series, hour)
        if close is not None and np.isfinite(spot) and spot > 0:
            ratios.append((h, close / float(spot)))
    return _basis_summary(ratios)


def hourly_rows(market: lr.Market, s: int, i: int, *, hours: int = HOURLY_ROWS) -> list[tuple]:
    """Closed 1h candles built from complete groups of four 15m bars, as visible after bar ``i`` closes."""

    now = int(market.grid_open[i]) + BAR
    last_end = now - now % 3_600
    g0 = int(market.grid_open[0])
    rows = []
    for h in range(hours, 0, -1):
        start = last_end - h * 3_600
        k = (start - g0) // BAR
        if k < 0 or (start - g0) % BAR or k + 3 > i:
            continue
        o, hi, lo, c = (market.open[s, k], market.high[s, k:k + 4], market.low[s, k:k + 4], market.close[s, k + 3])
        if not (np.isfinite(o) and np.isfinite(hi).all() and np.isfinite(lo).all() and np.isfinite(c)):
            continue
        rows.append((start, float(o), float(hi.max()), float(lo.min()), float(c)))
    return rows


def track_rows(market: lr.Market, s: int, i: int) -> list[tuple]:
    """15m candles after the alert (bars i+1 .. i+72h) as (open_time, o, h, l, c)."""

    a, b = i + 1, min(market.n_grid, i + 1 + TRACK_BARS)
    rows = []
    for k in range(a, b):
        o, h, low, c = market.open[s, k], market.high[s, k], market.low[s, k], market.close[s, k]
        if np.isfinite(o) and np.isfinite(h) and np.isfinite(low) and np.isfinite(c):
            rows.append((int(market.grid_open[k]), float(o), float(h), float(low), float(c)))
    return rows


def stop_outcome(entry: dict, rows: Sequence[tuple], *, resolvable_until: int,
                 data_continues: bool = False) -> dict[str, Any]:
    """Live tracking of one radar entry (stop or 72 hours).

    UNRESOLVABLE unless the stop was reached first: when the download ends
    before 72 hours, or when the data has a hole and resumes later (a hole is
    not a delisting). Data that simply stops is a delisting or halt: the live
    rule exits at the last known price after the grace period.
    """

    expires = int(entry["opened_at"]) + long_alerts.TRACK_HOURS * 3_600
    usable = [r for r in rows if r[0] + BAR <= resolvable_until]
    tracked, event = long_alerts.track_entry(
        entry, usable, now=expires + long_alerts.STALE_GRACE_HOURS * 3_600,
    )
    if event != long_alerts.STOPPED and (resolvable_until < expires or (tracked.get("note") and data_continues)):
        return {"status": "UNRESOLVABLE", "result_pct": None, "closed_at": None, "note": ""}
    window = [r for r in usable if r[0] + BAR <= expires]
    price = float(entry["entry_price"])
    return {
        "status": tracked["status"],
        "result_pct": _f(tracked.get("result_pct")),
        "closed_at": tracked.get("closed_at"),
        "note": tracked.get("note", ""),
        "mfe_pct": (max(r[2] for r in window) / price - 1.0) * 100.0 if window else None,
        "mae_pct": (min(r[3] for r in window) / price - 1.0) * 100.0 if window else None,
    }


# ---------------------------------------------------------------------------
# Likit-100: the live alert rule over replayed steps
# ---------------------------------------------------------------------------


def _bench(market: lr.Market, universe: Sequence[int], i: int, bars: int, min_members: int) -> float | None:
    returns, _ = lr.forward_returns(market, universe, i, bars)
    finite = returns[np.isfinite(returns)]
    return float(finite.mean()) if len(finite) >= min_members else None


def likit_rows(
    market: lr.Market,
    steps: Sequence[lr.Step | None],
    *,
    params: lr.RadarParams = lr.RadarParams(),
    funding: Mapping[str, tuple[list[int], list[float]]] | None = None,
    perp: Mapping[str, tuple[np.ndarray, np.ndarray]] | None = None,
    window: tuple[int, int] = (DISCOVERY_START, DISCOVERY_END),
) -> list[dict[str, Any]]:
    """One row per alert the live rule would send: in the top 3 and ``can_open`` for the symbol.

    The rule's state (open entry, 12h cooldown) also runs over the data before
    ``window`` (the warm-up months), so the first alerts in the window are the
    ones live would have sent; only alerts inside the window become rows.
    """

    last: dict[int, dict[str, Any]] = {}     # symbol index -> its latest radar entry
    rows: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        if step is None or not step.top:
            continue
        now = int(market.grid_open[i]) + BAR
        if now - 1 >= window[1]:
            break
        in_window = now - 1 >= window[0]
        context: dict[str, Any] | None = None
        ranks = {s: k for k, s in enumerate(step.universe, 1)}
        for rank, s in enumerate(step.top, 1):
            symbol = market.symbols[s]
            prev = last.get(s)
            view = [] if prev is None else [{
                "source": "LIKIT100", "symbol": symbol, "opened_at": prev["opened_at"],
                "status": long_alerts.OPEN if (prev["closed_at"] or math.inf) > now else "CLOSED",
            }]
            if not long_alerts.can_open(view, "LIKIT100", symbol, now=now):
                continue
            price = float(market.close[s, i])
            plan = long_alerts.compute_stop_plan(hourly_rows(market, s, i), price, now=now)
            entry = long_alerts.open_entry(source="LIKIT100", symbol=symbol, now=now, gate_status="REPLAY",
                                           detail="", plan=plan, entry_price=price)
            if plan is None:
                outcome = {"status": "NO_PLAN", "result_pct": None, "closed_at": now, "note": ""}
            else:
                edge = int(market.grid_open[min(int(market.coverage_end[s]), market.n_grid - 1)]) + BAR
                outcome = stop_outcome(entry, track_rows(market, s, i), resolvable_until=edge,
                                       data_continues=int(market.last_index[s]) > i + TRACK_BARS)
            last[s] = {"opened_at": now, "closed_at": outcome["closed_at"] if outcome["status"] != "UNRESOLVABLE" else math.inf}
            if not in_window:
                continue
            if context is None:
                context = market_features(market, step, i)
            row: dict[str, Any] = {"symbol": symbol, "decided_at": now - 1, "rank": rank, "liq_rank": ranks.get(s)}
            row.update(context)
            row.update(_coin_features(market, step, i, s, params, ranks.get(s), context))
            row.update(funding_at(funding, symbol, now - 1))
            row.update(basis_at(perp, market, s, i))
            row["stop_pct"] = plan.stop_pct if plan else None
            row["atr1h_pct"] = plan.atr_pct if plan else None
            row["structure_broken"] = (plan.technical_invalidation >= price) if plan else None
            row.update({f"stop72_{k}": v for k, v in outcome.items() if k != "closed_at"})
            exit_bars = None
            if outcome.get("closed_at") and outcome["status"] in (long_alerts.STOPPED, long_alerts.EXPIRED):
                exit_bars = max(1, (int(outcome["closed_at"]) - now) // BAR)
            row["stop72_bars"] = exit_bars
            row["stop72_bench_pct"] = (
                _bench(market, step.universe, i, exit_bars, params.min_benchmark_members) if exit_bars else None
            )
            for h in HORIZONS_H:
                bars = h * 3_600 // BAR
                own = lr.forward_return(market, s, i, bars)
                row[f"ret{h}_pct"] = own[0] if own else None
                row[f"bench{h}_pct"] = _bench(market, step.universe, i, bars, params.min_benchmark_members)
            row["can_authorize_trade"] = False
            rows.append(row)
    return rows


def _coin_features(market: lr.Market, step: lr.Step, i: int, s: int, params: lr.RadarParams,
                   liq_rank: int | None, context: Mapping[str, Any]) -> dict[str, Any]:
    metrics = lr.metrics_at(market, s, i)
    ticker = lr._ticker(market, s, i, params.assumed_spread_bps)
    technical = score_technical_long(
        ticker, metrics, liquidity_rank=liq_rank or params.universe_size,
        max_drawdown_pct=params.max_24h_drawdown_pct, max_gain_pct=params.max_24h_gain_pct,
        max_spread_bps=params.max_spread_bps,
    )
    chg24 = _f(market.chg24[s, i])
    chg7d = _change(market, s, i, 7 * DAY_BARS)
    qv24 = _f(market.qv24[s, i])
    out = {
        "tech_score": _f(technical.get("score")),
        "chg_1h": _f(metrics.get("change_1h_pct")),
        "chg_4h": _f(metrics.get("change_4h_pct")),
        "chg_24h": chg24,
        "chg_7d": chg7d,
        "ema20_dist": _f(metrics.get("ema20_distance_pct")),
        "ema20_slope": _f(metrics.get("ema20_slope_pct")),
        "rsi14": _f(metrics.get("rsi14")),
        "atr15_pct": _f(metrics.get("atr_pct")),
        "vol_ratio": _f(metrics.get("volume_ratio")),
        "range_pos": _f(metrics.get("range_position_pct")),
        "dd_from_high": _f(metrics.get("drawdown_from_high_pct")),
        "sma20d_dist": _f(_sma_distance(market, np.array([s]), i)[0]),
        "log_qv24": math.log10(qv24) if qv24 and qv24 > 0 else None,
    }
    out["rel_24h"] = None if chg24 is None or context.get("basket_24h") is None else chg24 - context["basket_24h"]
    out["rel_7d"] = None if chg7d is None or context.get("basket_7d") is None else chg7d - context["basket_7d"]
    return out


# ---------------------------------------------------------------------------
# Tactical: features and outcomes per alert
# ---------------------------------------------------------------------------


def _series_rows(series: tr._Series, lo: int, hi: int) -> list[tuple]:
    """(open_time, o, h, l, c) for candles with lo <= open_time and close_time < hi."""

    a = bisect_left(series.open_time, lo)
    b = bisect_left(series.close_time, hi)
    return [(series.open_time[k], series.open[k], series.high[k], series.low[k], series.close[k]) for k in range(a, b)]


def _close_at(series: tr._Series, t: int) -> float | None:
    j = series.last_closed_index(t)
    return series.close[j] if j >= 0 else None


def _daily_trend(series: tr._Series, t: int, days: int = 20) -> dict[str, float | None]:
    j = series.last_closed_index(t)
    if j + 1 < days or t - series.close_time[j] > 2 * 86_400:
        return {"sma20d": None, "7d": None, "30d": None}
    closes = series.close[j + 1 - days:j + 1]
    out = {"sma20d": (closes[-1] / statistics.fmean(closes) - 1.0) * 100.0}
    for name, n in (("7d", 7), ("30d", 30)):
        out[name] = (series.close[j] / series.close[j - n] - 1.0) * 100.0 if j - n >= 0 else None
    return out


def tactical_basis(perp: Mapping[str, tuple[np.ndarray, np.ndarray]] | None, h1: tr._Series, symbol: str,
                   t: int) -> dict[str, Any]:
    """Basis for BTC/ETH from the spot and perpetual 1h candles closed at ``t``."""

    if perp is None:
        return {"basis_status": "NOT_LOADED", "basis_pct": None, "basis_24h": None}
    series = perp.get(symbol)
    if series is None or not len(series[0]):
        return {"basis_status": "NO_PERP", "basis_pct": None, "basis_24h": None}
    last_end = (t + 1) - (t + 1) % 3_600
    ratios = []
    for h in range(1, 25):
        hour = last_end - h * 3_600
        j = bisect_left(h1.open_time, hour)
        if j >= len(h1) or h1.open_time[j] != hour or h1.close_time[j] > t:
            continue
        close = _perp_close(series, hour)
        if close is not None:
            ratios.append((h, close / h1.close[j]))
    return _basis_summary(ratios)


def load_tactical_perp(perp_dir: Path, *, end: int) -> tuple[dict, dict]:
    """BTC/ETH perpetual 1h closes and funding from ``binance_vision`` parquet files (sealed)."""

    import pandas as pd

    perp, funding = {}, {}
    for symbol in TACTICAL_SYMBOLS:
        frame = pd.read_parquet(perp_dir / f"{symbol}_klines_1h.parquet").sort_values("open_time")
        check_sealed(int(frame["close_time"].max()), end=end)
        perp[symbol] = (frame["open_time"].to_numpy(dtype=np.int64), frame["close"].to_numpy(dtype=float))
        path = perp_dir / f"{symbol}_funding.parquet"
        if path.exists():
            rows = pd.read_parquet(path).sort_values("funding_time")
            check_sealed(int(rows["available_at"].max()), end=end)
            funding[symbol] = ([int(x) for x in rows["available_at"]], [float(x) for x in rows["funding_rate"]])
    return perp, funding


def tactical_rows(
    records: Sequence[Any],
    data: Mapping[str, Mapping[TacticalTimeframe, tr._Series]],
    *,
    perp: Mapping[str, tuple[np.ndarray, np.ndarray]] | None = None,
    funding: Mapping[str, tuple[list[int], list[float]]] | None = None,
    window: tuple[int, int] = (DISCOVERY_START, DISCOVERY_END),
) -> list[dict[str, Any]]:
    """One row per tactical alert (a forward-ledger record), with three exits side by side."""

    end = min(max(s.close_time[-1] for s in frames.values() if len(s)) for frames in data.values()) + 1
    last: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda r: (r.decided_at, r.symbol)):
        t = int(record.decided_at)
        if not window[0] <= t < window[1]:
            continue
        frames = data[record.symbol]
        m5, m15, h1, h4 = (frames[tf] for tf in (TacticalTimeframe.M5, TacticalTimeframe.M15,
                                                  TacticalTimeframe.H1, TacticalTimeframe.H4))
        price = _close_at(m5, t)
        if price is None:
            continue
        now = t + 1
        engine = long_alerts.plan_from_levels(price, record.technical_invalidation, record.hard_stop,
                                              last_candle_open=t)
        hours = _series_rows(h1, now - (HOURLY_ROWS + 1) * 3_600, now)
        atr_plan = long_alerts.compute_stop_plan(hours, price, now=now)
        track = _series_rows(m15, now, now + long_alerts.TRACK_HOURS * 3_600 + 1)
        row: dict[str, Any] = {
            "symbol": record.symbol, "decided_at": t, "setup": record.setup,
            "state": record.state_at_record, "structure_4h": record.structure_4h,
        }
        prev = last.get(record.symbol)
        view = [] if prev is None else [{
            "source": "TAKTIK", "symbol": record.symbol, "opened_at": prev["opened_at"],
            "status": long_alerts.OPEN if (prev["closed_at"] or math.inf) > now else "CLOSED",
        }]
        row["radar_log_eligible"] = long_alerts.can_open(view, "TAKTIK", record.symbol, now=now)
        risk = (record.entry_high - record.hard_stop) / record.entry_high * 100.0
        row["ledger_risk_pct"] = risk
        row["cost_to_risk"] = TACTICAL_COST_PCT / risk if risk > 0 else None
        row["rr1"] = (record.target_1 - record.entry_high) / (record.entry_high - record.hard_stop)
        row["entry_gap_pct"] = (price / record.entry_high - 1.0) * 100.0
        row["engine_stop_pct"] = engine.stop_pct if engine else None
        row["atr1h_pct"] = atr_plan.atr_pct if atr_plan else None
        row["atr_stop_pct"] = atr_plan.stop_pct if atr_plan else None
        row["chg_24h"] = _pct(price, _close_at(m5, t - 86_400))
        row["chg_4h"] = _pct(price, _close_at(m5, t - 4 * 3_600))
        own = _daily_trend(frames[TacticalTimeframe.D1], t)
        btc = _daily_trend(data["BTCUSDT"][TacticalTimeframe.D1], t)
        row.update({"sma20d_dist": own["sma20d"], "chg_7d": own["7d"], "chg_30d": own["30d"],
                    "btc_sma20d": btc["sma20d"], "btc_7d": btc["7d"], "btc_30d": btc["30d"]})
        j4 = h4.last_closed_index(t)
        row.update(tactical_basis(perp, h1, record.symbol, t))
        row.update(funding_at(funding, record.symbol, t))
        row["h4_sma50_dist"] = (
            (h4.close[j4] / statistics.fmean(h4.close[j4 - 49:j4 + 1]) - 1.0) * 100.0 if j4 >= 49 else None
        )
        outcome = tr.realized(record, TACTICAL_COST_PCT)
        row["ledger_status"] = record.status
        row["ledger_r"] = outcome[1] if outcome else None
        for name, plan in (("engine", engine), ("atr", atr_plan)):
            if plan is None:
                row.update({f"{name}72_status": "NO_PLAN", f"{name}72_result_pct": None, f"{name}72_r": None})
                continue
            entry = long_alerts.open_entry(source="TAKTIK", symbol=record.symbol, now=now, gate_status="REPLAY",
                                           detail="", plan=plan, entry_price=price)
            result = stop_outcome(entry, track, resolvable_until=end)
            net = None if result["result_pct"] is None else result["result_pct"] - TACTICAL_COST_PCT
            row.update({
                f"{name}72_status": result["status"], f"{name}72_result_pct": result["result_pct"],
                f"{name}72_r": None if net is None else net / (plan.stop_pct + TACTICAL_COST_PCT),
            })
            if name == "engine":
                row["mfe72_pct"], row["mae72_pct"] = result.get("mfe_pct"), result.get("mae_pct")
                if row["radar_log_eligible"]:
                    closed = result["closed_at"] if result["status"] != "UNRESOLVABLE" else math.inf
                    last[record.symbol] = {"opened_at": now, "closed_at": closed}
        if engine is None and row["radar_log_eligible"]:
            last[record.symbol] = {"opened_at": now, "closed_at": now}
        for h in HORIZONS_H:
            exit_t = t + h * 3_600
            row[f"ret{h}_pct"] = _pct(_close_at(m5, exit_t), price) if exit_t < end else None
        row["can_authorize_trade"] = False
        rows.append(row)
    return rows


def _pct(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None or b <= 0 else (a / b - 1.0) * 100.0


# ---------------------------------------------------------------------------
# Outcome definitions and bucket tables (descriptive; no verdicts)
# ---------------------------------------------------------------------------


def _net(cost: float, *keys: str, minus: str | None = None) -> Callable[[Mapping[str, Any]], float | None]:
    def value(row: Mapping[str, Any]) -> float | None:
        raw = row.get(keys[0])
        if raw is None:
            return None
        out = raw - cost
        if minus is not None:
            bench = row.get(minus)
            if bench is None:
                return None
            out -= bench
        return out
    return value


LIKIT_OUTCOMES: dict[str, Callable[[Mapping[str, Any]], float | None]] = {
    "stop72_net": _net(LIKIT_COST_PCT, "stop72_result_pct"),
    "stop72_excess": _net(LIKIT_COST_PCT, "stop72_result_pct", minus="stop72_bench_pct"),
    "ret24_excess": _net(LIKIT_COST_PCT, "ret24_pct", minus="bench24_pct"),
    "ret4_excess": _net(LIKIT_COST_PCT, "ret4_pct", minus="bench4_pct"),
}
LIKIT_FEATURES = (
    "liq_rank", "tech_score", "chg_1h", "chg_4h", "chg_24h", "chg_7d", "ema20_dist", "ema20_slope",
    "rsi14", "atr15_pct", "vol_ratio", "range_pos", "dd_from_high", "sma20d_dist", "log_qv24", "rel_24h",
    "rel_7d", "btc_24h", "btc_7d", "btc_30d", "btc_sma20d", "basket_24h", "basket_7d", "breadth_sma20d",
    "funding_last", "funding_3d", "basis_pct", "basis_24h", "stop_pct", "atr1h_pct",
)
LIKIT_CATEGORIES = ("rank", "regime", "funding_status", "basis_status", "structure_broken", "stop72_status")

TACTICAL_OUTCOMES: dict[str, Callable[[Mapping[str, Any]], float | None]] = {
    "ledger_r": lambda r: r.get("ledger_r"),
    "engine72_r": lambda r: r.get("engine72_r"),
    "atr72_r": lambda r: r.get("atr72_r"),
    "ret24_net": _net(TACTICAL_COST_PCT, "ret24_pct"),
}
TACTICAL_FEATURES = (
    "ledger_risk_pct", "cost_to_risk", "rr1", "entry_gap_pct", "engine_stop_pct", "atr1h_pct", "atr_stop_pct",
    "chg_4h", "chg_24h", "chg_7d", "chg_30d", "sma20d_dist", "h4_sma50_dist", "btc_sma20d", "btc_7d", "btc_30d",
    "basis_pct", "basis_24h", "funding_last", "funding_3d",
)
TACTICAL_CATEGORIES = ("setup", "symbol", "state", "structure_4h", "ledger_status", "radar_log_eligible",
                       "basis_status", "funding_status")


def day_cluster_ci(values: Sequence[float], times: Sequence[int], *, alpha: float = 0.05,
                   seed: int = 0) -> tuple[float, float] | None:
    return lr.day_cluster_ci(values, [t // 86_400 for t in times], alpha=alpha, n_resamples=2000, seed=seed)


def quantile_edges(values: Sequence[float], q: int = 5) -> list[float]:
    """Interior bucket edges from the discovery rows only (never from the window being tested)."""

    finite = sorted(v for v in values if v is not None and math.isfinite(v))
    if len(finite) < q:
        return []
    edges = [float(np.quantile(finite, k / q)) for k in range(1, q)]
    return sorted(set(edges))


def bucket_of(value: float | None, edges: Sequence[float]) -> int | None:
    return None if value is None else bisect_right(edges, value)


def _summary(rows: Sequence[Mapping[str, Any]], outcomes: Mapping[str, Callable], half_at: int) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(rows)}
    for name, fn in outcomes.items():
        pairs = [(fn(r), int(r["decided_at"])) for r in rows]
        pairs = [(v, t) for v, t in pairs if v is not None]
        values = [v for v, _ in pairs]
        out[name] = {
            "n": len(values),
            "mean": statistics.fmean(values) if values else None,
            "ci95": day_cluster_ci(values, [t for _, t in pairs]) if len(values) >= 30 else None,
            "positive": sum(v > 0 for v in values) / len(values) if values else None,
            "h1": _mean_where(pairs, lambda t: t < half_at),
            "h2": _mean_where(pairs, lambda t: t >= half_at),
        }
    return out


def _mean_where(pairs: Sequence[tuple[float, int]], keep: Callable[[int], bool]) -> float | None:
    values = [v for v, t in pairs if keep(t)]
    return statistics.fmean(values) if values else None


def describe(rows: Sequence[Mapping[str, Any]], *, features: Sequence[str], categories: Sequence[str],
             outcomes: Mapping[str, Callable], q: int = 5) -> dict[str, Any]:
    """Outcome means by feature quintile (and by category); a missing feature is its own bucket."""

    rows = sorted(rows, key=lambda r: (r["decided_at"], r["symbol"]))
    half_at = int(rows[len(rows) // 2]["decided_at"]) if rows else 0
    report: dict[str, Any] = {"all": _summary(rows, outcomes, half_at), "half_at": half_at,
                              "features": {}, "categories": {}}
    for feature in features:
        edges = quantile_edges([r.get(feature) for r in rows], q)
        buckets: dict[str, list[Mapping[str, Any]]] = {}
        for r in rows:
            k = bucket_of(r.get(feature), edges)
            buckets.setdefault("n/a" if k is None else f"q{k + 1}", []).append(r)
        report["features"][feature] = {
            "edges": edges,
            "buckets": {name: _summary(b, outcomes, half_at) for name, b in sorted(buckets.items())},
        }
    for category in categories:
        buckets = {}
        for r in rows:
            buckets.setdefault(str(r.get(category)), []).append(r)
        report["categories"][category] = {name: _summary(b, outcomes, half_at) for name, b in sorted(buckets.items())}
    return report


def _fmt(value: Any, spec: str = "+.2f") -> str:
    return "n/a" if value is None else format(value, spec)


def print_description(title: str, report: Mapping[str, Any], outcomes: Sequence[str]) -> None:
    print(f"## {title}")
    head = f"{'bucket':<26}{'n':>7}" + "".join(f"{o:>16}{'ci95':>17}{'h1/h2':>13}" for o in outcomes)
    print(head)

    def line(name: str, summary: Mapping[str, Any]) -> None:
        cells = []
        for o in outcomes:
            s = summary[o]
            ci = "n/a" if s["ci95"] is None else f"[{s['ci95'][0]:+.2f},{s['ci95'][1]:+.2f}]"
            cells.append(f"{_fmt(s['mean'], '+.3f'):>16}{ci:>17}{_fmt(s['h1'], '+.2f'):>7}/{_fmt(s['h2'], '+.2f'):<5}")
        print(f"{name:<26}{summary['n']:>7}" + "".join(cells))

    line("ALL", report["all"])
    for feature, info in report["features"].items():
        edges = ", ".join(f"{e:.3g}" for e in info["edges"])
        print(f"-- {feature} (edges: {edges})")
        for name, summary in info["buckets"].items():
            line(f"  {name}", summary)
    for category, buckets in report["categories"].items():
        print(f"-- {category}")
        for name, summary in buckets.items():
            line(f"  {name[:24]}", summary)


# ---------------------------------------------------------------------------
# Loading with the seal
# ---------------------------------------------------------------------------


def load_likit_market(data_dir: Path, *, end: int) -> tuple[lr.Market, dict, dict[str, str]]:
    """``liquid_replay.load_market`` minus pairs that are not ordinary coins in Binance history.

    The live identity rules miss some historical pairs (PAX, UST, AUD, BULL/BEAR,
    wrapped coins); the history identity layer removes them before anything is
    ranked, so they can be neither a signal nor part of the benchmark.
    """

    import pandas as pd

    from trading.data.binance_history_identity import exclusion_reason
    from trading.data.binance_universe import coverage_end

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
        frame = pd.read_parquet(data_dir / "15m" / f"{symbol}.parquet", columns=list(lr.COLUMNS))
        columns[symbol] = {name: frame[name].to_numpy() for name in lr.COLUMNS}
        year, month = (int(x) for x in info["months"][-1].split("-"))
        coverage[symbol] = coverage_end((year, month)) - (BAR - 1)
    market = lr.build_market(columns, coverage_end_time=coverage)
    check_sealed(int(market.grid_open[-1]) + BAR - 1, end=end)
    return market, manifest, excluded


def load_tactical_data(data_dir: Path, *, end: int) -> dict:
    data = tr.load_dataset(data_dir)
    last_close = max(series.close_time[-1] for frames in data.values() for series in frames.values() if len(series))
    check_sealed(last_close, end=end)
    return data


def write_rows(rows: Sequence[Mapping[str, Any]], path: Path) -> None:
    columns: list[str] = []
    for row in rows:
        columns.extend(k for k in row if k not in columns)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(["" if row.get(c) is None else row.get(c) for c in columns])
    tmp.replace(path)


def _write_json(payload: Mapping[str, Any], path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str), "utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Step 2: pre-registered tests; step 3: the one-shot confirmation
# ---------------------------------------------------------------------------

FILTER_FAMILY = "signal_quality_filters"
CONFIRMATION_MONTHS = 27          # 2024-06 .. 2026-08: warm-up before 2024-09, then the window
HALF_AT = 1_756_684_800           # 2025-09-01T00:00:00Z: fixed split of the confirmation window
CONFIRM_MIN_N = 100
FILTER_TESTS = ("F1_LIKIT_CALM_NOT_CHASING", "F2_TACTICAL_PERP_BELOW_SPOT")
FILTER_ALPHA = 0.05 / len(FILTER_TESTS)
# Outcomes last up to 72h and the random-hour control overlaps for three days:
# resample contiguous 7-day blocks, never single days (AGENTS.md §7).
BLOCK_DAYS = 7
# Above these shares the run reports INCOMPLETE_DATA instead of a verdict (AGENTS.md §2).
COMPLETENESS = {
    "max_skipped_step_share": 0.02,
    "max_engine_error_share": 0.01,
    "max_unknown_filter_share": 0.05,
    "max_missing_outcome_share": 0.05,
}
LIKIT_ATR15_MAX = 1.0             # % (15m ATR(14) / price), discovery: below the 2nd quintile edge 1.12
LIKIT_REL24_MAX = 5.0             # percentage points above the universe median 24h change
TACTICAL_BASIS_MAX = 0.0          # keep only while the perpetual trades below spot
LIKIT_STRESS_COST_PCT = lr.ReplayCosts().round_trip_pct(fee_mult=1.5, slip_mult=2.0)
TACTICAL_STRESS_COST_PCT = tr.ReplayCosts().round_trip_pct(fee_mult=1.5, slip_mult=2.0)
FILTER_DATASET = {
    "source": "binance_vision_spot_and_um_perpetual",
    "confirmation_window": "2024-09..2026-08",
    "built_as_of": CONFIRMATION_END,
}

# Everything that produces a signal, a feature used by a test, or an outcome.
FINGERPRINT_FILES = (
    "trading/backtest/signal_quality.py",
    "trading/backtest/liquid_replay.py",
    "trading/backtest/tactical_replay.py",
    "trading/data/universe_funding.py",
    "trading/data/binance_universe.py",
    "trading/data/binance_vision.py",
    "trading/data/binance_carry_universe.py",
    "trading/data/binance_history_identity.py",
    "acce_unified/liquid_long.py",
    "acce_unified/cex.py",
    "acce_unified/models.py",
    "acce_unified/tactical_long.py",
    "acce_unified/tactical_long_data.py",
    "acce_unified/tactical_long_engine.py",
    "acce_unified/forward_ledger.py",
)
# long_alerts also formats Telegram text; only its rules are part of the trial.
LONG_ALERT_LOGIC = ("closed_candles", "StopPlan", "compute_stop_plan", "plan_from_levels", "can_open",
                    "open_entry", "track_entry")
LONG_ALERT_CONSTANTS = ("STOP_INTERVAL_SECONDS", "STOP_LOOKBACK", "ATR_PERIOD", "BUFFER_ATR", "MIN_STOP_ATR",
                        "RISK_PER_TRADE_PCT", "TRACK_INTERVAL_SECONDS", "TRACK_HOURS", "COOLDOWN_HOURS",
                        "STALE_GRACE_HOURS")
REPO_ROOT = Path(__file__).resolve().parents[2]


def logic_fingerprint(repo_root: Path = REPO_ROOT) -> str:
    digest = hashlib.sha256()
    for name in FINGERPRINT_FILES:
        digest.update(name.encode())
        digest.update((repo_root / name).read_bytes())
    for name in LONG_ALERT_LOGIC:
        digest.update(inspect.getsource(getattr(long_alerts, name)).encode())
    for name in LONG_ALERT_CONSTANTS:
        digest.update(f"{name}={getattr(long_alerts, name)!r}".encode())
    return digest.hexdigest()[:16]


def likit_calm_not_chasing(row: Mapping[str, Any]) -> bool | None:
    """F1: 15m ATR ≤ 1.0% and a 24h gain at most 5 points above the universe median; None if unknown."""

    atr, rel = row.get("atr15_pct"), row.get("rel_24h")
    if atr is None or rel is None:
        return None
    return atr <= LIKIT_ATR15_MAX and rel <= LIKIT_REL24_MAX


def tactical_perp_below_spot(row: Mapping[str, Any]) -> bool | None:
    """F2: keep a BTC/ETH alert only while the perpetual trades below spot; None if the basis is unknown."""

    basis = row.get("basis_pct")
    return None if basis is None else basis < TACTICAL_BASIS_MAX


def likit_net(row: Mapping[str, Any], cost: float = LIKIT_COST_PCT) -> float | None:
    result = row.get("stop72_result_pct")
    return None if result is None else result - cost


def likit_excess(row: Mapping[str, Any], cost: float = LIKIT_COST_PCT) -> float | None:
    result, bench = row.get("stop72_result_pct"), row.get("stop72_bench_pct")
    return None if result is None or bench is None else result - cost - bench


def tactical_r(row: Mapping[str, Any], stop: str, cost: float = TACTICAL_COST_PCT) -> float | None:
    """R of the stop + 72h exit with stop rule ``stop`` ('engine' or 'atr') at round-trip ``cost``."""

    result, stop_pct = row.get(f"{stop}72_result_pct"), row.get(f"{stop}_stop_pct")
    if result is None or stop_pct is None:
        return None
    return (result - cost) / (stop_pct + cost)


Pairs = list[tuple[float, int]]


def _pairs(rows: Iterable[Mapping[str, Any]], value: Callable[[Mapping[str, Any]], float | None]) -> Pairs:
    out = []
    for row in rows:
        v = value(row)
        if v is not None and math.isfinite(v):
            out.append((float(v), int(row["decided_at"])))
    return out


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


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


def group_summary(pairs: Pairs, *, alpha: float = FILTER_ALPHA) -> dict[str, Any]:
    values = [v for v, _ in pairs]
    return {
        "n": len(values),
        "mean": _mean(values),
        "ci": _interval(block_bootstrap_means([pairs])[:, 0], alpha) if len(values) >= 2 else None,
        "h1": _mean([v for v, t in pairs if t < HALF_AT]),
        "h2": _mean([v for v, t in pairs if t >= HALF_AT]),
    }


def diff_ci(a: Pairs, b: Pairs, *, alpha: float = FILTER_ALPHA, n_resamples: int = 4000,
            seed: int = 0) -> tuple[float, float] | None:
    """CI of mean(a) − mean(b), both groups resampled with the same day blocks."""

    if len(a) < 2 or len(b) < 2:
        return None
    means = block_bootstrap_means([a, b], n_resamples=n_resamples, seed=seed)
    return _interval(means[:, 0] - means[:, 1], alpha)


def difference(a: Pairs, b: Pairs, *, alpha: float = FILTER_ALPHA) -> dict[str, Any]:
    def half(pairs: Pairs, first: bool) -> list[float]:
        return [v for v, t in pairs if (t < HALF_AT) == first]

    def gap(x: list[float], y: list[float]) -> float | None:
        return None if not x or not y else statistics.fmean(x) - statistics.fmean(y)

    return {
        "mean": gap([v for v, _ in a], [v for v, _ in b]),
        "ci": diff_ci(a, b, alpha=alpha),
        "h1": gap(half(a, True), half(b, True)),
        "h2": gap(half(a, False), half(b, False)),
    }


def _positive(summary: Mapping[str, Any]) -> bool:
    """Lower bound > 0 and both halves > 0."""

    ci = summary.get("ci")
    return bool(ci) and ci[0] > 0 and (summary.get("h1") or 0) > 0 and (summary.get("h2") or 0) > 0


def filter_verdict(net: Mapping[str, Any], excess: Mapping[str, Any], stress: Mapping[str, Any],
                   kept_vs_dropped: Mapping[str, Any]) -> str:
    """PASS: kept alerts make money and beat their benchmark; KAYBI_AZALTIR: only the worse ones are separated."""

    if net["n"] >= CONFIRM_MIN_N and _positive(net) and _positive(excess) and (stress.get("mean") or 0) > 0:
        return "PASS"
    if _positive(kept_vs_dropped):
        return "KAYBI_AZALTIR"
    return "NO_EFFECT"


def completeness_problems(*, steps: int, skipped_steps: int, alerts: int, unknown_filter: int,
                          missing_outcome: int, evaluated: int | None = None, engine_errors: int = 0) -> list[str]:
    """Reasons the run cannot give a verdict; empty when the data is complete enough."""

    problems = []
    if alerts == 0:
        problems.append("no alerts in the confirmation window")
    if steps == 0 or skipped_steps > COMPLETENESS["max_skipped_step_share"] * steps:
        problems.append(f"skipped steps {skipped_steps}/{steps}")
    if evaluated is not None and engine_errors > COMPLETENESS["max_engine_error_share"] * max(1, evaluated):
        problems.append(f"engine errors {engine_errors}/{evaluated}")
    if unknown_filter > COMPLETENESS["max_unknown_filter_share"] * max(1, alerts):
        problems.append(f"unknown filter value {unknown_filter}/{alerts}")
    if missing_outcome > COMPLETENESS["max_missing_outcome_share"] * max(1, alerts):
        problems.append(f"missing outcome {missing_outcome}/{alerts}")
    return problems


def apply_completeness(result: dict[str, Any], problems: Sequence[str]) -> dict[str, Any]:
    if problems:
        result = dict(result, verdict="INCOMPLETE_DATA", problems=list(problems))
    return result


def evaluate_f1(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    kept = [r for r in rows if likit_calm_not_chasing(r) is True]
    dropped = [r for r in rows if likit_calm_not_chasing(r) is False]
    net, excess = group_summary(_pairs(kept, likit_net)), group_summary(_pairs(kept, likit_excess))
    stress = group_summary(_pairs(kept, lambda r: likit_net(r, LIKIT_STRESS_COST_PCT)))
    gap = difference(_pairs(kept, likit_net), _pairs(dropped, likit_net))
    return {
        "test": FILTER_TESTS[0], "alerts": len(rows), "kept": len(kept), "dropped": len(dropped),
        "unknown": len(rows) - len(kept) - len(dropped), "kept_net": net, "kept_excess": excess,
        "kept_stress_net": stress, "dropped_net": group_summary(_pairs(dropped, likit_net)),
        "kept_minus_dropped_net": gap, "verdict": filter_verdict(net, excess, stress, gap),
    }


def evaluate_f2(rows: Sequence[Mapping[str, Any]], baseline: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    kept = [r for r in rows if tactical_perp_below_spot(r) is True]
    dropped = [r for r in rows if tactical_perp_below_spot(r) is False]
    base = [r for r in baseline if tactical_perp_below_spot(r) is True]

    def atr_r(row: Mapping[str, Any]) -> float | None:
        return tactical_r(row, "atr")

    net = group_summary(_pairs(kept, atr_r))
    excess = difference(_pairs(kept, atr_r), _pairs(base, atr_r))
    stress = group_summary(_pairs(kept, lambda r: tactical_r(r, "atr", TACTICAL_STRESS_COST_PCT)))
    gap = difference(_pairs(kept, atr_r), _pairs(dropped, atr_r))
    return {
        "test": FILTER_TESTS[1], "alerts": len(rows), "kept": len(kept), "dropped": len(dropped),
        "unknown": len(rows) - len(kept) - len(dropped), "baseline_kept": len(base), "kept_r": net,
        "kept_minus_random_hours_r": excess, "kept_stress_r": stress,
        "dropped_r": group_summary(_pairs(dropped, atr_r)), "kept_minus_dropped_r": gap,
        "verdict": filter_verdict(net, excess, stress, gap),
    }


def tactical_baseline_rows(
    data: Mapping[str, Mapping[TacticalTimeframe, tr._Series]],
    *,
    perp: Mapping[str, tuple[np.ndarray, np.ndarray]] | None,
    window: tuple[int, int],
    step: int = 3_600,
) -> list[dict[str, Any]]:
    """Random-timing control: an entry at every hourly close, with the ATR stop + 72h rule."""

    end = min(max(s.close_time[-1] for s in frames.values() if len(s)) for frames in data.values()) + 1
    rows = []
    for symbol in TACTICAL_SYMBOLS:
        frames = data[symbol]
        m5, m15, h1 = frames[TacticalTimeframe.M5], frames[TacticalTimeframe.M15], frames[TacticalTimeframe.H1]
        for t in range(window[0] - 1 + step, window[1], step):
            price = _close_at(m5, t)
            if price is None or t - m5.close_time[m5.last_closed_index(t)] > 600:
                continue
            now = t + 1
            plan = long_alerts.compute_stop_plan(_series_rows(h1, now - (HOURLY_ROWS + 1) * 3_600, now), price, now=now)
            row: dict[str, Any] = {"symbol": symbol, "decided_at": t}
            row.update(tactical_basis(perp, h1, symbol, t))
            if plan is None:
                continue
            entry = long_alerts.open_entry(source="TAKTIK", symbol=symbol, now=now, gate_status="BASELINE",
                                           detail="", plan=plan, entry_price=price)
            result = stop_outcome(entry, _series_rows(m15, now, now + long_alerts.TRACK_HOURS * 3_600 + 1),
                                  resolvable_until=end)
            row.update({"atr_stop_pct": plan.stop_pct, "atr72_status": result["status"],
                        "atr72_result_pct": result["result_pct"], "can_authorize_trade": False})
            rows.append(row)
    return rows


def filter_trial_params() -> dict[str, Any]:
    return {
        "code_fingerprint": logic_fingerprint(),
        "tests": {
            FILTER_TESTS[0]: {"keep_if": {"atr15_pct_max": LIKIT_ATR15_MAX, "rel_24h_max": LIKIT_REL24_MAX},
                              "outcome": "live stop + 72h rule, net and excess vs equal-weight top-100"},
            FILTER_TESTS[1]: {"keep_if": {"basis_pct_below": TACTICAL_BASIS_MAX},
                              "outcome": "ATR stop + 72h R; excess vs hourly random entries with the same filter"},
        },
        "window": [DISCOVERY_END, CONFIRMATION_END],
        "half_at": HALF_AT,
        "family_alpha": FILTER_ALPHA,
        "bootstrap": {"method": "circular_moving_block_calendar_days", "block_days": BLOCK_DAYS, "resamples": 4000},
        "completeness": COMPLETENESS,
        "min_n": CONFIRM_MIN_N,
        "costs_pct": {"likit": LIKIT_COST_PCT, "likit_stress": LIKIT_STRESS_COST_PCT,
                      "tactical": TACTICAL_COST_PCT, "tactical_stress": TACTICAL_STRESS_COST_PCT},
        "verdicts": "PASS / KAYBI_AZALTIR / NO_EFFECT; INCOMPLETE_DATA gives no verdict",
    }


def filter_trial_id() -> str:
    from trading.research.robustness import trial_id_for

    return trial_id_for(family=FILTER_FAMILY, params=filter_trial_params(), dataset=FILTER_DATASET)


def require_registration(registry_path: Path) -> str:
    """The confirmation runs only for code whose tests were registered before (fail closed)."""

    from trading.research.robustness import TrialRegistry

    trial_id = filter_trial_id()
    known = {r.trial_id for r in TrialRegistry(registry_path).selection_trials(FILTER_FAMILY)}
    if trial_id not in known:
        raise SystemExit(f"trial {trial_id} is not pre-registered for this code; refusing to look at 2024-09+")
    return trial_id


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_data(radar: str, out_dir: Path, *, decision_at: int = DISCOVERY_END,
               months: int = DISCOVERY_MONTHS, perp_data: bool = True) -> dict:
    """Download data as of ``decision_at``: later candles are never fetched."""

    if radar == "likit":
        from trading.data.binance_universe import build_universe
        from trading.data.universe_funding import build_funding, build_perp_hourly

        manifest = build_universe(out_dir, lookback_months=months, decision_at=decision_at)
        out = {
            "window": manifest["window"], "candidates": manifest["candidates"],
            "stopped": len(manifest["stopped_before_window_end"]),
        }
        if perp_data:
            funding = build_funding(out_dir, out_dir / "funding", decision_at=decision_at)
            perp = build_perp_hourly(out_dir, out_dir / "funding", decision_at=decision_at)
            out["funding"] = {k: funding[k] for k in ("with_perp", "with_rows", "rows", "ambiguous")}
            out["perp_1h"] = {k: perp[k] for k in ("with_perp", "with_rows", "rows", "ambiguous")}
        return out
    from trading.data.binance_perp import Timeframe
    from trading.data.binance_vision import download_symbol

    timeframes = tuple(Timeframe(tr.FILE_TIMEFRAME[tf]) for tf in tr.REQUIRED_TIMEFRAMES)
    for symbol in tr.SYMBOLS:
        download_symbol(symbol, out_dir=out_dir, decision_at=decision_at, timeframes=timeframes,
                        lookback_months=months, market="spot", include_funding=False)
    for symbol in TACTICAL_SYMBOLS:
        download_symbol(symbol, out_dir=out_dir / "perp", decision_at=decision_at, timeframes=(Timeframe("1h"),),
                        lookback_months=months, market="futures/um", include_funding=True)
    return {"symbols": list(tr.SYMBOLS), "perpetuals": list(TACTICAL_SYMBOLS)}


def build_discovery_data(radar: str, out_dir: Path) -> dict:
    return build_data(radar, out_dir)


def _fmt_group(name: str, g: Mapping[str, Any]) -> str:
    ci = g.get("ci")
    ci_text = "n/a" if not ci else f"[{ci[0]:+.3f},{ci[1]:+.3f}]"
    n = f" n={g['n']}" if "n" in g else ""
    return f"  {name:<28}{n} mean={_fmt(g.get('mean'), '+.3f')} ci={ci_text} h1={_fmt(g.get('h1'), '+.3f')} h2={_fmt(g.get('h2'), '+.3f')}"


def print_confirmation(results: Sequence[Mapping[str, Any]]) -> None:
    for result in results:
        print(f"## {result['test']}: {result['verdict']}")
        for key, value in result.items():
            if isinstance(value, Mapping):
                print(_fmt_group(key, value))
            elif key not in {"test", "verdict"}:
                print(f"  {key}: {value}")


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse
    import logging

    parser = argparse.ArgumentParser(description="Signal-quality feature table (discovery window only).")
    sub = parser.add_subparsers(dest="radar", required=True)
    build = sub.add_parser("build", help="download the sealed discovery data")
    build.add_argument("which", choices=("likit", "tactical"))
    build.add_argument("--out", type=Path, required=True)
    build_confirm = sub.add_parser("build-confirm", help="download the confirmation data (registered trial only)")
    build_confirm.add_argument("which", choices=("likit", "tactical"))
    build_confirm.add_argument("--out", type=Path, required=True)
    build_confirm.add_argument("--trial-registry", type=Path, default=Path("research/trials/registry.jsonl"))
    confirm = sub.add_parser("confirm", help="run the pre-registered tests once on 2024-09..2026-08")
    confirm.add_argument("which", choices=("likit", "tactical"))
    confirm.add_argument("--data-dir", type=Path, required=True)
    confirm.add_argument("--perp-dir", type=Path, default=None)
    confirm.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    confirm.add_argument("--out-dir", type=Path, default=Path("research/data/signal_quality_confirm"))
    confirm.add_argument("--trial-registry", type=Path, default=Path("research/trials/registry.jsonl"))
    likit = sub.add_parser("likit")
    likit.add_argument("--data-dir", type=Path, default=Path("research/data/sq_liquid_universe"))
    likit.add_argument("--funding-dir", type=Path, default=None)
    likit.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    tactical = sub.add_parser("tactical")
    tactical.add_argument("--data-dir", type=Path, default=Path("research/data/sq_binance_spot"))
    tactical.add_argument("--perp-dir", type=Path, default=None)
    tactical.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    for p in (likit, tactical):
        p.add_argument("--out-dir", type=Path, default=Path("research/data/signal_quality"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    started = time.time()
    if args.radar == "build":
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        print(json.dumps(build_discovery_data(args.which, args.out), indent=1))
        return 0
    if args.radar == "build-confirm":
        trial = require_registration(args.trial_registry)
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        summary = build_data(args.which, args.out, decision_at=CONFIRMATION_END, months=CONFIRMATION_MONTHS,
                             perp_data=False)
        print(json.dumps({"trial": trial, **summary}, indent=1))
        return 0
    if args.radar == "confirm":
        return _confirm(args, started)
    window = (DISCOVERY_START, DISCOVERY_END)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.radar == "likit":
        from trading.data.universe_funding import load_funding, load_perp_hourly

        market, _, excluded = load_likit_market(args.data_dir, end=DISCOVERY_END)
        print(f"excluded (history identity): {json.dumps(excluded, sort_keys=True)}")
        funding = load_funding(args.funding_dir) if args.funding_dir else None
        perp = load_perp_hourly(args.funding_dir) if args.funding_dir else None
        steps = lr.evaluate(market, lr.RadarParams(), workers=args.workers, progress=True)
        rows = likit_rows(market, steps, funding=funding, perp=perp, window=window)
        report = describe(rows, features=LIKIT_FEATURES, categories=LIKIT_CATEGORIES, outcomes=LIKIT_OUTCOMES)
        outcomes = list(LIKIT_OUTCOMES)
        title = f"Likit-100 alerts, discovery window ({len(rows)} rows, cost {LIKIT_COST_PCT:.2f}% round trip)"
    else:
        data = load_tactical_data(args.data_dir, end=DISCOVERY_END)
        phase1 = tr.evaluate(data, spread_bps=2.0, workers=args.workers, progress=True)
        end_time = tr.decision_times(data)[-1]
        records = tr.record_outcomes(phase1.events, data, end_time=end_time)
        perp, funding = load_tactical_perp(args.perp_dir, end=DISCOVERY_END) if args.perp_dir else (None, None)
        rows = tactical_rows(records, data, perp=perp, funding=funding, window=window)
        report = describe(rows, features=TACTICAL_FEATURES, categories=TACTICAL_CATEGORIES,
                          outcomes=TACTICAL_OUTCOMES, q=3)
        outcomes = list(TACTICAL_OUTCOMES)
        title = f"Tactical alerts, discovery window ({len(rows)} rows, cost {TACTICAL_COST_PCT:.2f}% round trip)"
    write_rows(rows, args.out_dir / f"{args.radar}_rows.csv.gz")
    _write_json({"window": list(window), "report": report, "can_authorize_trade": False},
                args.out_dir / f"{args.radar}_description.json")
    print_description(title, report, outcomes)
    print(f"wrote {args.out_dir} in {time.time() - started:.0f}s")
    return 0


def _confirm(args: Any, started: float) -> int:
    trial = require_registration(args.trial_registry)
    window = (DISCOVERY_END, CONFIRMATION_END)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.which == "likit":
        market, _, excluded = load_likit_market(args.data_dir, end=CONFIRMATION_END)
        print(f"excluded (history identity): {json.dumps(excluded, sort_keys=True)}")
        steps = lr.evaluate(market, lr.RadarParams(), workers=args.workers, progress=True)
        rows = likit_rows(market, steps, window=window)
        baseline: list[dict[str, Any]] = []
        in_window = [k for k in range(market.n_grid)
                     if window[0] <= int(market.grid_open[k]) + BAR - 1 < window[1]]
        problems = completeness_problems(
            steps=len(in_window), skipped_steps=sum(steps[k] is None for k in in_window), alerts=len(rows),
            unknown_filter=sum(likit_calm_not_chasing(r) is None for r in rows),
            missing_outcome=sum(likit_net(r) is None for r in rows),
        )
        results = [apply_completeness(evaluate_f1(rows), problems)]
    else:
        if args.perp_dir is None:
            raise SystemExit("--perp-dir is required: F2 needs the basis")
        data = load_tactical_data(args.data_dir, end=CONFIRMATION_END)
        perp, funding = load_tactical_perp(args.perp_dir, end=CONFIRMATION_END)
        phase1 = tr.evaluate(data, spread_bps=2.0, workers=args.workers, progress=True)
        records = tr.record_outcomes(phase1.events, data, end_time=tr.decision_times(data)[-1])
        rows = tactical_rows(records, data, perp=perp, funding=funding, window=window)
        baseline = tactical_baseline_rows(data, perp=perp, window=window)
        problems = completeness_problems(
            steps=phase1.evaluated + phase1.skipped_data, skipped_steps=phase1.skipped_data, alerts=len(rows),
            unknown_filter=sum(tactical_perp_below_spot(r) is None for r in rows),
            missing_outcome=sum(tactical_r(r, "atr") is None for r in rows),
            evaluated=phase1.evaluated, engine_errors=phase1.engine_errors,
        )
        print(f"phase 1: evaluated={phase1.evaluated} skipped_data={phase1.skipped_data} "
              f"engine_errors={phase1.engine_errors} warmup={phase1.skipped_warmup}")
        results = [apply_completeness(evaluate_f2(rows, baseline), problems)]
    write_rows(rows, args.out_dir / f"{args.which}_confirm_rows.csv.gz")
    if baseline:
        write_rows(baseline, args.out_dir / "tactical_confirm_baseline.csv.gz")
    _write_json({"trial": trial, "window": list(window), "results": results, "can_authorize_trade": False},
                args.out_dir / f"{args.which}_confirmation.json")
    print(f"trial {trial} (pre-registered) · window 2024-09..2026-08 · alpha {FILTER_ALPHA:.4f}")
    print_confirmation(results)
    print(f"wrote {args.out_dir} in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
