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
    window: tuple[int, int] = (DISCOVERY_START, DISCOVERY_END),
) -> list[dict[str, Any]]:
    """One row per alert the live rule would send: in the top 3 and ``can_open`` for the symbol."""

    last: dict[int, dict[str, Any]] = {}     # symbol index -> its latest radar entry
    rows: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        if step is None or not step.top:
            continue
        now = int(market.grid_open[i]) + BAR
        if not window[0] <= now - 1 < window[1]:
            continue
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
            if context is None:
                context = market_features(market, step, i)
            price = float(market.close[s, i])
            plan = long_alerts.compute_stop_plan(hourly_rows(market, s, i), price, now=now)
            entry = long_alerts.open_entry(source="LIKIT100", symbol=symbol, now=now, gate_status="REPLAY",
                                           detail="", plan=plan, entry_price=price)
            row: dict[str, Any] = {"symbol": symbol, "decided_at": now - 1, "rank": rank, "liq_rank": ranks.get(s)}
            row.update(context)
            row.update(_coin_features(market, step, i, s, params, ranks.get(s), context))
            row.update(funding_at(funding, symbol, now - 1))
            row["stop_pct"] = plan.stop_pct if plan else None
            row["atr1h_pct"] = plan.atr_pct if plan else None
            row["structure_broken"] = (plan.technical_invalidation >= price) if plan else None
            if plan is None:
                outcome = {"status": "NO_PLAN", "result_pct": None, "closed_at": now, "note": ""}
            else:
                edge = int(market.grid_open[min(int(market.coverage_end[s]), market.n_grid - 1)]) + BAR
                outcome = stop_outcome(entry, track_rows(market, s, i), resolvable_until=edge,
                                       data_continues=int(market.last_index[s]) > i + TRACK_BARS)
            last[s] = {"opened_at": now, "closed_at": outcome["closed_at"] if outcome["status"] != "UNRESOLVABLE" else math.inf}
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


def tactical_rows(
    records: Sequence[Any],
    data: Mapping[str, Mapping[TacticalTimeframe, tr._Series]],
    *,
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
    "funding_last", "funding_3d", "stop_pct", "atr1h_pct",
)
LIKIT_CATEGORIES = ("rank", "regime", "funding_status", "structure_broken", "stop72_status")

TACTICAL_OUTCOMES: dict[str, Callable[[Mapping[str, Any]], float | None]] = {
    "ledger_r": lambda r: r.get("ledger_r"),
    "engine72_r": lambda r: r.get("engine72_r"),
    "atr72_r": lambda r: r.get("atr72_r"),
    "ret24_net": _net(TACTICAL_COST_PCT, "ret24_pct"),
}
TACTICAL_FEATURES = (
    "ledger_risk_pct", "cost_to_risk", "rr1", "entry_gap_pct", "engine_stop_pct", "atr1h_pct", "atr_stop_pct",
    "chg_4h", "chg_24h", "chg_7d", "chg_30d", "sma20d_dist", "h4_sma50_dist", "btc_sma20d", "btc_7d", "btc_30d",
)
TACTICAL_CATEGORIES = ("setup", "symbol", "state", "structure_4h", "ledger_status", "radar_log_eligible")


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
# CLI
# ---------------------------------------------------------------------------


def build_discovery_data(radar: str, out_dir: Path) -> dict:
    """Download the discovery data as of ``DISCOVERY_END``: later candles are never fetched."""

    if radar == "likit":
        from trading.data.binance_universe import build_universe
        from trading.data.universe_funding import build_funding

        manifest = build_universe(out_dir, lookback_months=DISCOVERY_MONTHS, decision_at=DISCOVERY_END)
        funding = build_funding(out_dir, out_dir / "funding", decision_at=DISCOVERY_END)
        return {
            "window": manifest["window"], "candidates": manifest["candidates"],
            "stopped": len(manifest["stopped_before_window_end"]),
            "funding": {k: funding[k] for k in ("with_perp", "with_rows", "rows", "ambiguous")},
        }
    from trading.data.binance_perp import Timeframe
    from trading.data.binance_vision import download_symbol

    timeframes = tuple(Timeframe(tr.FILE_TIMEFRAME[tf]) for tf in tr.REQUIRED_TIMEFRAMES)
    for symbol in tr.SYMBOLS:
        download_symbol(symbol, out_dir=out_dir, decision_at=DISCOVERY_END, timeframes=timeframes,
                        lookback_months=DISCOVERY_MONTHS, market="spot", include_funding=False)
    return {"symbols": list(tr.SYMBOLS)}


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse
    import logging

    parser = argparse.ArgumentParser(description="Signal-quality feature table (discovery window only).")
    sub = parser.add_subparsers(dest="radar", required=True)
    build = sub.add_parser("build", help="download the sealed discovery data")
    build.add_argument("which", choices=("likit", "tactical"))
    build.add_argument("--out", type=Path, required=True)
    likit = sub.add_parser("likit")
    likit.add_argument("--data-dir", type=Path, default=Path("research/data/sq_liquid_universe"))
    likit.add_argument("--funding-dir", type=Path, default=None)
    likit.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    tactical = sub.add_parser("tactical")
    tactical.add_argument("--data-dir", type=Path, default=Path("research/data/sq_binance_spot"))
    tactical.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    for p in (likit, tactical):
        p.add_argument("--out-dir", type=Path, default=Path("research/data/signal_quality"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    started = time.time()
    if args.radar == "build":
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        print(json.dumps(build_discovery_data(args.which, args.out), indent=1))
        return 0
    window = (DISCOVERY_START, DISCOVERY_END)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.radar == "likit":
        from trading.data.universe_funding import load_funding

        market, _, excluded = load_likit_market(args.data_dir, end=DISCOVERY_END)
        print(f"excluded (history identity): {json.dumps(excluded, sort_keys=True)}")
        funding = load_funding(args.funding_dir) if args.funding_dir else None
        steps = lr.evaluate(market, lr.RadarParams(), workers=args.workers, progress=True)
        rows = likit_rows(market, steps, funding=funding, window=window)
        report = describe(rows, features=LIKIT_FEATURES, categories=LIKIT_CATEGORIES, outcomes=LIKIT_OUTCOMES)
        outcomes = list(LIKIT_OUTCOMES)
        title = f"Likit-100 alerts, discovery window ({len(rows)} rows, cost {LIKIT_COST_PCT:.2f}% round trip)"
    else:
        data = load_tactical_data(args.data_dir, end=DISCOVERY_END)
        phase1 = tr.evaluate(data, spread_bps=2.0, workers=args.workers, progress=True)
        end_time = tr.decision_times(data)[-1]
        records = tr.record_outcomes(phase1.events, data, end_time=end_time)
        rows = tactical_rows(records, data, window=window)
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
