"""Diagnostic audit of the carry-replay data. Changes no verdict.

STATIC_CARRY showed a -83% drawdown for a delta-neutral book. That is either
real (short squeezes, deeply negative funding) or a data artefact (a perp
mapped to the wrong spot pair, a ticker reused for a different coin, legs
that stop at different times). This module answers which, without touching
the pre-registered replay: it reads the same data with the same loader and
the same point-in-time universe, and lists

- the worst and best single position-periods (symbol, time, both legs);
- perp/spot price-ratio breaks (a ratio that leaves its own running median
  by more than 10% is not a hedge any more);
- the symbols that drove each year's carry result;
- extreme funding prints.

Research only; no order authority (AGENTS.md §4).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from trading.backtest import carry_replay as cr

RATIO_BREAK = 0.10          # perp/spot ratio vs its trailing median
RATIO_WINDOW = 21           # 7 days of 8h candles
FUNDING_EXTREME_PCT = 0.5   # per 8h window


def _day(t: int) -> str:
    return datetime.fromtimestamp(int(t), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


@dataclass(frozen=True)
class PositionPeriod:
    time: str
    symbol: str
    total_pct: float
    basis_pct: float
    funding_pct: float
    perp_ret_pct: float
    spot_ret_pct: float


def position_periods(market: cr.CarryMarket) -> list[tuple[int, int, float, float, float, float]]:
    """(k, s, basis, funding, perp return, spot return) for every STATIC universe member and period."""

    volume = cr.trailing_volume(market)
    rows = []
    for k in range(market.n - 1):
        for s in cr.universe_at(market, volume, k):
            p0, s0 = market.perp_open[s, k], market.spot_open[s, k]
            p1, s1 = market.perp_open[s, k + 1], market.spot_open[s, k + 1]
            if not (math.isfinite(p1) and math.isfinite(s1)):
                continue
            perp_ret, spot_ret = (p1 / p0 - 1) * 100, (s1 / s0 - 1) * 100
            rows.append((k, s, spot_ret - perp_ret, market.funding[s, k] * 100, perp_ret, spot_ret))
    return rows


def ratio_breaks(market: cr.CarryMarket) -> list[dict]:
    """Symbols whose perp/spot close ratio leaves its trailing median by more than RATIO_BREAK."""

    out = []
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = market.perp_close / market.spot_close
    for s, symbol in enumerate(market.symbols):
        r = ratio[s]
        idx = np.nonzero(np.isfinite(r))[0]
        if len(idx) < RATIO_WINDOW + 1:
            continue
        values = r[idx]
        breaks = []
        for j in range(RATIO_WINDOW, len(values)):
            median = float(np.median(values[j - RATIO_WINDOW:j]))
            deviation = values[j] / median - 1.0
            if abs(deviation) > RATIO_BREAK:
                breaks.append((int(idx[j]), deviation))
        if breaks:
            worst = max(breaks, key=lambda b: abs(b[1]))
            out.append({
                "symbol": symbol, "breaks": len(breaks),
                "first": _day(market.grid_open[breaks[0][0]]), "last": _day(market.grid_open[breaks[-1][0]]),
                "worst_time": _day(market.grid_open[worst[0]]), "worst_deviation_pct": round(worst[1] * 100, 2),
                "median_ratio": round(float(np.median(values)), 6),
            })
    return sorted(out, key=lambda b: -b["breaks"])


def _plain(value):
    """numpy scalars (np.int64 from counting np.bool_, np.float64 from round) as JSON-safe Python types."""

    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def audit(market: cr.CarryMarket, *, top: int = 25) -> dict:
    rows = position_periods(market)
    as_period = [PositionPeriod(_day(market.grid_open[k]), market.symbols[s], round(b + f, 4), round(b, 4),
                                round(f, 4), round(pr, 4), round(sr, 4)) for k, s, b, f, pr, sr in rows]
    ordered = sorted(as_period, key=lambda p: p.total_pct)
    by_year_symbol: dict[str, dict[str, float]] = {}
    for (k, s, b, f, _, _) in rows:
        year = _day(market.grid_open[k])[:4]
        bucket = by_year_symbol.setdefault(year, {})
        # contribution to the equal-weight STATIC book (20 positions)
        bucket[market.symbols[s]] = bucket.get(market.symbols[s], 0.0) + (b + f) / cr.UNIVERSE_SIZE
    years = {}
    for year, contributions in sorted(by_year_symbol.items()):
        ranked = sorted(contributions.items(), key=lambda kv: kv[1])
        years[year] = {
            "total_pct": round(sum(contributions.values()), 2),
            "worst": [(sym, round(v, 2)) for sym, v in ranked[:8]],
            "best": [(sym, round(v, 2)) for sym, v in ranked[-5:][::-1]],
        }
    worst_total = sum(p.total_pct for p in ordered[:top]) / cr.UNIVERSE_SIZE
    extremes = [p for p in as_period if abs(p.funding_pct) >= FUNDING_EXTREME_PCT]
    return _plain({
        "position_periods": len(as_period),
        "book_total_pct": round(sum(p.total_pct for p in as_period) / cr.UNIVERSE_SIZE, 2),
        "worst_top_share_pct": round(worst_total, 2),
        "worst": [asdict(p) for p in ordered[:top]],
        "best": [asdict(p) for p in ordered[-top:][::-1]],
        "by_year": years,
        "ratio_breaks": ratio_breaks(market),
        "funding_extremes": {
            "count": len(extremes),
            "negative": sum(p.funding_pct < 0 for p in extremes),
            "worst": [asdict(p) for p in sorted(extremes, key=lambda p: p.funding_pct)[:top]],
        },
        "can_authorize_trade": False,
    })


def print_audit(report: dict) -> None:
    print(f"position-periods={report['position_periods']} STATIC book total={report['book_total_pct']}% "
          f"(worst {len(report['worst'])} periods alone: {report['worst_top_share_pct']}%)")
    print("worst position-periods (time, symbol, total, basis, funding, perp%, spot%):")
    for p in report["worst"]:
        print(f"  {p['time']} {p['symbol']:<16}{p['total_pct']:+9.2f}{p['basis_pct']:+9.2f}{p['funding_pct']:+8.3f}"
              f"{p['perp_ret_pct']:+9.2f}{p['spot_ret_pct']:+9.2f}")
    print("best position-periods:")
    for p in report["best"][:10]:
        print(f"  {p['time']} {p['symbol']:<16}{p['total_pct']:+9.2f}{p['basis_pct']:+9.2f}{p['funding_pct']:+8.3f}"
              f"{p['perp_ret_pct']:+9.2f}{p['spot_ret_pct']:+9.2f}")
    print("by year (STATIC book contribution, %):")
    for year, info in report["by_year"].items():
        print(f"  {year}: total {info['total_pct']:+.2f} · worst {info['worst']} · best {info['best']}")
    breaks = report["ratio_breaks"]
    print(f"perp/spot ratio breaks (>{RATIO_BREAK:.0%} from 7-day median): {len(breaks)} symbols")
    for b in breaks[:30]:
        print(f"  {b['symbol']:<16} breaks={b['breaks']:<5} {b['first']} → {b['last']} "
              f"worst {b['worst_deviation_pct']:+.1f}% at {b['worst_time']} (median ratio {b['median_ratio']})")
    fx = report["funding_extremes"]
    print(f"funding prints ≥{FUNDING_EXTREME_PCT}%/8h on held pairs: {fx['count']} ({fx['negative']} negative)")
    for p in fx["worst"][:15]:
        print(f"  {p['time']} {p['symbol']:<16} funding {p['funding_pct']:+.3f}% basis {p['basis_pct']:+.2f}")


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Audit the carry replay data (diagnostic only).")
    parser.add_argument("--data-dir", type=Path, default=Path("research/data/carry_universe"))
    parser.add_argument("--out", type=Path, default=Path("research/data/carry_audit.json"))
    args = parser.parse_args(list(argv) if argv is not None else None)
    market, _ = cr.load_market(args.data_dir)
    report = audit(market)
    print_audit(report)  # first, so the log keeps the result even if writing fails
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1), "utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
