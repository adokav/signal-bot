"""Weekly cross-sectional momentum on the Likit-100 universe. Research only.

Protocol and decision rules: docs/WEEKLY_MOMENTUM.md (written before any
result). Every Monday 00:00 UTC the point-in-time top 100 (the live radar's
``select_liquid_universe`` on the trailing 24h volume of closed candles) is
ranked by its trailing L-week return; the top 20% is held for H weeks with
one cohort per week (Jegadeesh-Titman), so the weekly return series does not
overlap. The benchmark is the equal-weight top 100, rebalanced weekly.

- **Point in time:** ranking and universe use candles closed at the Monday
  bar; returns start at that close.
- **Survivorship:** a pair that stops trading for good is frozen at its last
  close; a hole that data later resumes from, or the end of what was
  downloaded, is unknown (excluded and counted), never a flat price.
- **Continuity:** a hole longer than a day that data later resumes from is a
  break (token swaps and redenominations such as COCOS 2021-01 and SUN
  2021-06 reuse the ticker at a price 1000x off; LUNA 2.0 reused LUNA's).
  No return or ranking is computed across a break; exchange-wide maintenance
  holes are a few hours long and are not breaks.
- **Sealed windows:** discovery data is built as of 2024-09-01; the
  confirmation runs once, after at most two variants are pre-registered.

No order authority anywhere (AGENTS.md §4, §10).
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from acce_unified.liquid_long import select_liquid_universe
from trading.backtest import liquid_replay as lr
from trading.backtest import signal_quality as sq


WEEK = 7 * 86_400
WEEK_BARS = WEEK // lr.BAR_SECONDS
LOOKBACKS_W = (1, 2, 4)
HOLDS_W = (1, 4)
TOP_SHARE = 0.2
MIN_RANKED = 50
COST_PCT = lr.ReplayCosts().round_trip_pct()                                    # 0.20
STRESS_COST_PCT = lr.ReplayCosts().round_trip_pct(fee_mult=1.5, slip_mult=2.0)   # 0.35
BLOCK_WEEKS = 4
DISCOVERY = (sq.DISCOVERY_START, sq.DISCOVERY_END)
CONFIRMATION = (sq.DISCOVERY_END, sq.CONFIRMATION_END)
HALF_AT = sq.HALF_AT                       # 2025-09-01: fixed split of the confirmation window
MIN_WEEKS = 80
MAX_UNKNOWN_MEMBER_WEEKS = 0.02
MAX_SKIPPED_WEEKS = 0.05


# ---------------------------------------------------------------------------
# Point-in-time universe, prices and ranking
# ---------------------------------------------------------------------------


def monday_steps(market: lr.Market, window: tuple[int, int]) -> list[int]:
    """Grid index of the 15m bar that closes at each Monday 00:00 UTC inside ``window``."""

    out = []
    for i, opened in enumerate(market.grid_open):
        decided = int(opened) + lr.BAR_SECONDS
        if decided % 86_400 == 0 and datetime.fromtimestamp(decided, tz=timezone.utc).weekday() == 0:
            if window[0] <= decided < window[1]:
                out.append(i)
    return out


def universe_at(market: lr.Market, i: int, params: lr.RadarParams = lr.RadarParams()) -> list[int]:
    """The live radar's top 100 at bar ``i`` (same as ``liquid_replay.evaluate_step``)."""

    available = np.isfinite(market.qv24[:, i]) & np.isfinite(market.chg24[:, i]) & np.isfinite(market.close[:, i])
    tickers = [lr._ticker(market, int(s), i, params.assumed_spread_bps) for s in np.nonzero(available)[0]]
    universe = select_liquid_universe(tickers, size=params.universe_size, min_quote_volume=params.min_quote_volume)
    return [market.index_of[t.symbol] for t in universe]


Breaks = Mapping[int, np.ndarray]


def continuity_breaks(market: lr.Market) -> dict[int, np.ndarray]:
    """Per symbol, the sorted bars where data resumes after a hole longer than a day."""

    out = {}
    for s in range(len(market.symbols)):
        idx = np.nonzero(np.isfinite(market.close[s]))[0]
        resumed = idx[1:][np.diff(idx) - 1 > lr.DAY_BARS]
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


def price_at(market: lr.Market, s: int, i: int) -> float | None:
    """Close at bar ``i``; the last close for a pair that stopped trading for good; None if unknown."""

    if i < 0 or i >= market.n_grid:
        return None
    close = market.close[s, i]
    if np.isfinite(close):
        return float(close)
    last = int(market.last_index[s])
    if 0 <= last < i and last < int(market.coverage_end[s]) - lr.DAY_BARS:
        return float(market.close[s, last])            # delisted / halted: frozen at its last trade
    return None                                         # a hole or the download edge: unknown


def rank(market: lr.Market, members: Sequence[int], i: int, lookback_w: int,
         breaks: Breaks | None = None) -> list[tuple[float, int]] | None:
    """(trailing return %, symbol index), best first; None when fewer than ``MIN_RANKED`` can be ranked."""

    breaks = continuity_breaks(market) if breaks is None else breaks
    back = i - lookback_w * WEEK_BARS
    ranked = []
    for s in members:
        now, then = price_at(market, s, i), price_at(market, s, back)
        if (now is not None and then is not None and then > 0 and np.isfinite(market.close[s, i])
                and continuous(breaks, s, back, i)):
            ranked.append(((now / then - 1.0) * 100.0, s))
    if len(ranked) < MIN_RANKED:
        return None
    ranked.sort(key=lambda row: (-row[0], market.symbols[row[1]]))
    return ranked


def top_bottom(ranked: Sequence[tuple[float, int]]) -> tuple[list[int], list[int]]:
    n = max(1, int(round(TOP_SHARE * len(ranked))))
    return [s for _, s in ranked[:n]], [s for _, s in ranked[-n:]]


# ---------------------------------------------------------------------------
# Weekly series (one non-overlapping return per week)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cohort:
    formed: int                 # grid index of the formation bar
    members: tuple[int, ...]
    entry: tuple[float, ...]    # prices at formation


@dataclass(frozen=True)
class WeekRow:
    start: int                  # Monday 00:00 UTC that opens the week
    portfolio_pct: float
    benchmark_pct: float
    bottom_pct: float | None
    cost_pct: float
    cohorts: int
    member_weeks: int           # cohort and benchmark slots priced this week
    unknown_member_weeks: int

    @property
    def net_pct(self) -> float:
        return self.portfolio_pct - self.cost_pct

    @property
    def excess_pct(self) -> float:
        return self.portfolio_pct - self.cost_pct - self.benchmark_pct


def _cohort_week(market: lr.Market, cohort: Cohort, i0: int, i1: int,
                 breaks: Breaks) -> tuple[float | None, int]:
    """Buy-and-hold return of a cohort from bar ``i0`` to ``i1`` (weights drift from formation)."""

    weight_sum, value_sum, unknown = 0.0, 0.0, 0
    for s, entry in zip(cohort.members, cohort.entry):
        p0, p1 = price_at(market, s, i0), price_at(market, s, i1)
        if p0 is None or p1 is None or not continuous(breaks, s, cohort.formed, i1):
            unknown += 1
            continue
        weight = p0 / entry
        weight_sum += weight
        value_sum += weight * (p1 / p0)
    if weight_sum <= 0:
        return None, unknown
    return (value_sum / weight_sum - 1.0) * 100.0, unknown


def _equal_weight(market: lr.Market, members: Sequence[int], i0: int, i1: int,
                  breaks: Breaks) -> tuple[float | None, int]:
    returns, unknown = [], 0
    for s in members:
        p0, p1 = price_at(market, s, i0), price_at(market, s, i1)
        if p0 is None or p1 is None or not continuous(breaks, s, i0, i1):
            unknown += 1
            continue
        returns.append((p1 / p0 - 1.0) * 100.0)
    return (statistics.fmean(returns) if returns else None), unknown


def weekly_series(
    market: lr.Market,
    steps: Sequence[int],
    universes: Mapping[int, Sequence[int]],
    *,
    lookback_w: int,
    hold_w: int,
) -> tuple[list[WeekRow], int]:
    """Weekly rows and the number of weeks whose cohort could not be formed."""

    breaks = continuity_breaks(market)
    tops: dict[int, Cohort] = {}
    bottoms: dict[int, Cohort] = {}
    skipped = 0
    for w, i in enumerate(steps):
        ranked = rank(market, universes[i], i, lookback_w, breaks)
        if ranked is None:
            skipped += 1
            continue
        top, bottom = top_bottom(ranked)
        for target, members in ((tops, top), (bottoms, bottom)):
            target[w] = Cohort(i, tuple(members), tuple(price_at(market, s, i) for s in members))
    rows = []
    for k in range(len(steps) - 1):
        i0, i1 = steps[k], steps[k + 1]
        if i1 - i0 != WEEK_BARS:
            continue                                     # a gap in the Monday grid: no weekly return
        active = [tops[w] for w in range(k - hold_w + 1, k + 1) if w in tops]
        if not active:
            continue
        parts, unknown, slots = [], 0, len(universes[i0])
        for cohort in active:
            ret, missing = _cohort_week(market, cohort, i0, i1, breaks)
            unknown += missing
            slots += len(cohort.members)
            if ret is not None:
                parts.append(ret)
        bench, bench_unknown = _equal_weight(market, universes[i0], i0, i1, breaks)
        if not parts or bench is None:
            continue
        low = [b for w in range(k - hold_w + 1, k + 1) if (b := bottoms.get(w)) is not None]
        low_parts = [r for c in low if (r := _cohort_week(market, c, i0, i1, breaks)[0]) is not None]
        rows.append(WeekRow(
            start=int(market.grid_open[i0]) + lr.BAR_SECONDS,
            portfolio_pct=statistics.fmean(parts),
            benchmark_pct=bench,
            bottom_pct=statistics.fmean(low_parts) if low_parts else None,
            cost_pct=COST_PCT / hold_w,
            cohorts=len(parts),
            member_weeks=slots,
            unknown_member_weeks=unknown + bench_unknown,
        ))
    return rows, skipped


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def block_ci(values: Sequence[float], *, alpha: float, block: int = BLOCK_WEEKS, n_resamples: int = 4000,
             seed: int = 0) -> tuple[float, float] | None:
    """Circular moving-block bootstrap CI of the mean of a weekly series."""

    x = np.asarray(values, dtype=float)
    n = len(x)
    if n < 2 * block:
        return None
    blocks = -(-n // block)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(n_resamples, blocks))
    idx = ((starts[:, :, None] + np.arange(block)) % n).reshape(n_resamples, -1)[:, :n]
    means = x[idx].mean(axis=1)
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def summary(rows: Sequence[WeekRow], value, *, alpha: float, half_at: int) -> dict[str, Any]:
    values = [value(r) for r in rows]
    first = [value(r) for r in rows if r.start < half_at]
    second = [value(r) for r in rows if r.start >= half_at]
    return {
        "weeks": len(values),
        "mean": statistics.fmean(values) if values else None,
        "ci": block_ci(values, alpha=alpha),
        "h1": statistics.fmean(first) if first else None,
        "h2": statistics.fmean(second) if second else None,
        "positive_weeks": (sum(v > 0 for v in values) / len(values)) if values else None,
    }


def _positive(s: Mapping[str, Any]) -> bool:
    ci = s.get("ci")
    return bool(ci) and ci[0] > 0 and (s.get("h1") or 0) > 0 and (s.get("h2") or 0) > 0


def verdict(net: Mapping[str, Any], excess: Mapping[str, Any], stress: Mapping[str, Any], *,
            unknown_share: float, skipped_share: float) -> str:
    """docs/WEEKLY_MOMENTUM.md decision rules."""

    if unknown_share > MAX_UNKNOWN_MEMBER_WEEKS or skipped_share > MAX_SKIPPED_WEEKS:
        return "INCOMPLETE_DATA"
    if net["weeks"] >= MIN_WEEKS and _positive(net) and _positive(excess) and (stress.get("mean") or 0) > 0:
        return "PASS"
    if _positive(excess):
        return "SEPETI_YENER"
    if excess.get("ci") and excess["ci"][1] < 0:
        return "TERS_DONUS"
    return "NO_EFFECT"


def evaluate(market: lr.Market, steps: Sequence[int], universes: Mapping[int, Sequence[int]], *, lookback_w: int,
             hold_w: int, alpha: float, half_at: int) -> dict[str, Any]:
    rows, skipped = weekly_series(market, steps, universes, lookback_w=lookback_w, hold_w=hold_w)
    member_weeks = sum(r.member_weeks for r in rows) or 1
    unknown_share = sum(r.unknown_member_weeks for r in rows) / member_weeks
    skipped_share = skipped / max(1, len(steps))
    net = summary(rows, lambda r: r.net_pct, alpha=alpha, half_at=half_at)
    excess = summary(rows, lambda r: r.excess_pct, alpha=alpha, half_at=half_at)
    stress = summary(rows, lambda r: r.portfolio_pct - STRESS_COST_PCT / hold_w, alpha=alpha, half_at=half_at)
    spread_rows = [r for r in rows if r.bottom_pct is not None]
    spread = summary(spread_rows, lambda r: r.portfolio_pct - r.bottom_pct, alpha=alpha, half_at=half_at)
    return {
        "variant": f"L{lookback_w}_H{hold_w}",
        "lookback_weeks": lookback_w,
        "hold_weeks": hold_w,
        "net_pct": net,
        "excess_pct": excess,
        "stress_net_pct": stress,
        "benchmark_pct": summary(rows, lambda r: r.benchmark_pct, alpha=alpha, half_at=half_at),
        "top_minus_bottom_pct": spread,
        "skipped_weeks": skipped,
        "unknown_member_week_share": unknown_share,
        "verdict": verdict(net, excess, stress, unknown_share=unknown_share, skipped_share=skipped_share),
        "can_authorize_trade": False,
    }


# ---------------------------------------------------------------------------
# Pre-registration
# ---------------------------------------------------------------------------


FAMILY = "weekly_momentum_likit100"
FINGERPRINT_FILES = (
    "trading/backtest/weekly_momentum.py",
    "trading/backtest/liquid_replay.py",
    "trading/backtest/signal_quality.py",
    "trading/data/binance_universe.py",
    "trading/data/binance_vision.py",
    "trading/data/binance_history_identity.py",
    "acce_unified/liquid_long.py",
    "acce_unified/cex.py",
    "acce_unified/models.py",
)
REPO_ROOT = Path(__file__).resolve().parents[2]


def code_fingerprint(repo_root: Path = REPO_ROOT) -> str:
    digest = hashlib.sha256()
    for name in FINGERPRINT_FILES:
        digest.update(name.encode())
        digest.update((repo_root / name).read_bytes())
    return digest.hexdigest()[:16]


# Filled in only after the discovery window has been inspected (docs/WEEKLY_MOMENTUM.md).
REGISTERED_VARIANTS: tuple[tuple[int, int], ...] = ()


def trial_params() -> dict[str, Any]:
    return {
        "code_fingerprint": code_fingerprint(),
        "variants": [f"L{lb}_H{h}" for lb, h in REGISTERED_VARIANTS],
        "universe": "live select_liquid_universe top 100, min 1M USD, Monday 00:00 UTC",
        "top_share": TOP_SHARE,
        "min_ranked": MIN_RANKED,
        "costs_pct": {"base": COST_PCT, "stress": STRESS_COST_PCT, "per": "cohort round trip / hold weeks"},
        "window": list(CONFIRMATION),
        "half_at": HALF_AT,
        "family_alpha": 0.05 / max(1, len(REGISTERED_VARIANTS)),
        "bootstrap": {"method": "circular_moving_block_weeks", "block_weeks": BLOCK_WEEKS, "resamples": 4000},
        "min_weeks": MIN_WEEKS,
        "completeness": {"max_unknown_member_weeks": MAX_UNKNOWN_MEMBER_WEEKS, "max_skipped_weeks": MAX_SKIPPED_WEEKS},
        "verdicts": "PASS / SEPETI_YENER / TERS_DONUS / NO_EFFECT; INCOMPLETE_DATA gives no verdict",
    }


def trial_id() -> str:
    from trading.research.robustness import trial_id_for

    return trial_id_for(family=FAMILY, params=trial_params(),
                        dataset={"source": "binance_vision_spot", "confirmation_window": "2024-09..2026-08"})


def require_registration(registry: Path) -> str:
    from trading.research.robustness import TrialRegistry

    if not REGISTERED_VARIANTS:
        raise SystemExit("no variant is registered; the confirmation window stays sealed")
    tid = trial_id()
    if tid not in {r.trial_id for r in TrialRegistry(registry).selection_trials(FAMILY)}:
        raise SystemExit(f"trial {tid} is not pre-registered for this code; refusing to look at 2024-09+")
    return tid


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _fmt(value: Any, spec: str = "+.3f") -> str:
    return "n/a" if value is None else format(value, spec)


def print_result(result: Mapping[str, Any]) -> None:
    print(f"## {result['variant']}: {result['verdict']}  (skipped weeks {result['skipped_weeks']}, "
          f"unknown member-weeks {result['unknown_member_week_share']:.2%})")
    for key in ("net_pct", "excess_pct", "stress_net_pct", "benchmark_pct", "top_minus_bottom_pct"):
        s = result[key]
        ci = "n/a" if not s["ci"] else f"[{s['ci'][0]:+.3f},{s['ci'][1]:+.3f}]"
        print(f"  {key:<22} weeks={s['weeks']:>4} mean={_fmt(s['mean'])} ci={ci} h1={_fmt(s['h1'])} "
              f"h2={_fmt(s['h2'])} up={_fmt(s['positive_weeks'], '.0%')}")


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Weekly cross-sectional momentum (docs/WEEKLY_MOMENTUM.md).")
    sub = parser.add_subparsers(dest="mode", required=True)
    disc = sub.add_parser("discover", help="all six variants on the sealed discovery window")
    disc.add_argument("--data-dir", type=Path, required=True)
    build = sub.add_parser("build-confirm", help="download the confirmation data (registered trial only)")
    conf = sub.add_parser("confirm", help="the registered variants, once, on 2024-09..2026-08")
    conf.add_argument("--data-dir", type=Path, required=True)
    for p in (build, conf):
        p.add_argument("--trial-registry", type=Path, default=Path("research/trials/registry.jsonl"))
    for p in (disc, build, conf):
        p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)

    started = time.time()
    if args.mode == "build-confirm":
        import logging

        trial = require_registration(args.trial_registry)
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        built = sq.build_data("likit", args.out, decision_at=sq.CONFIRMATION_END, months=sq.CONFIRMATION_MONTHS,
                              perp_data=False)
        print(json.dumps({"trial": trial, **built}, indent=1))
        return 0
    if args.mode == "discover":
        market, _, excluded = sq.load_likit_market(args.data_dir, end=sq.DISCOVERY_END)
        window, variants = DISCOVERY, [(lb, h) for lb in LOOKBACKS_W for h in HOLDS_W]
        alpha, half_at, trial = 0.05, 1_661_990_400, None          # discovery halves split at 2022-09-01
    else:
        trial = require_registration(args.trial_registry)
        market, _, excluded = sq.load_likit_market(args.data_dir, end=sq.CONFIRMATION_END)
        window, variants = CONFIRMATION, list(REGISTERED_VARIANTS)
        alpha, half_at = 0.05 / len(variants), HALF_AT
    print(f"excluded (history identity): {json.dumps(excluded, sort_keys=True)}")
    steps = monday_steps(market, window)
    universes = {i: universe_at(market, i) for i in steps}
    results = [evaluate(market, steps, universes, lookback_w=lb, hold_w=h, alpha=alpha, half_at=half_at)
               for lb, h in variants]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_name(args.out.name + ".tmp")
    tmp.write_text(json.dumps({"mode": args.mode, "trial": trial, "window": list(window), "mondays": len(steps),
                               "results": results, "can_authorize_trade": False}, indent=1, default=str), "utf-8")
    tmp.replace(args.out)
    print(f"{args.mode}: {len(steps)} Mondays, alpha {alpha:.4f}" + (f", trial {trial} (pre-registered)" if trial else ""))
    for result in results:
        print_result(result)
    print(f"wrote {args.out} in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
