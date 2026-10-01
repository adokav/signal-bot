"""Perpetual-market signals on a point-in-time majors universe. Research only.

Protocol, thresholds and decision rules: docs/MAJORS_STUDY.md (written before
any result). Every month the universe is BTC, ETH, the next 10 seasoned spot
pairs by trailing 30-day volume that have a perpetual, and the largest meme
coin if none of those 10 is one. Every day at 00:00 UTC each member gets three
features from data visible by then:

- **F** funding: the 8h-equivalent mean rate paid over the last 72 hours;
- **T** taker flow: the 24h perpetual taker-buy share as a z-score against the
  same coin's previous 30 days;
- **O** open interest: the signs of the 24h OI (contracts) and spot price
  changes.

Outcomes: spot from the next 15m open to the close 24h or 72h later, net of
round-trip costs and in excess of the equal-weight majors basket over the same
span. Unknown inputs or outcomes are excluded and counted (AGENTS.md §2); a
token-swap gap is a break, not a return (``weekly_momentum.continuous``).

No order authority anywhere (AGENTS.md §4, §10).
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from trading.backtest import liquid_replay as lr
from trading.backtest import signal_quality as sq
from trading.backtest import weekly_momentum as wm
from trading.data.majors_data import MEMES


BAR = lr.BAR_SECONDS
DAY = 86_400
DECISION_START = 1_604_188_800                 # 2020-11-01: first month with 30 days of volume
DISCOVERY = (DECISION_START, sq.DISCOVERY_END)
CONFIRMATION = (sq.DISCOVERY_END, sq.CONFIRMATION_END)
DISCOVERY_HALF_AT = 1_661_990_400              # 2022-09-01
HALF_AT = sq.HALF_AT                           # 2025-09-01
OI_FROM = 1_640_995_200                        # 2022-01-01: OI dumps start 2021-12 for most pairs

FIXED = ("BTCUSDT", "ETHUSDT")
TOP_OTHERS = 10
MIN_MEMBERS = 12
VOLUME_DAYS = 30
MIN_VOLUME_DAYS = 28
SEASON_DAYS = 90
PERP_SEASON_DAYS = 30

HORIZONS_H = (24, 72)
COST_PCT = lr.ReplayCosts().round_trip_pct()                                   # 0.20
STRESS_COST_PCT = lr.ReplayCosts().round_trip_pct(fee_mult=1.5, slip_mult=2.0)  # 0.35
BASKET_MIN = 8

FUNDING_WINDOW = 72 * 3600
FUNDING_LAG = 60
FUNDING_MAX_GAP = 8 * 3600 + 60
FUNDING_HIGH = 0.0003
FLOW_HOURS = 24
FLOW_BASELINE_DAYS = 30
FLOW_MIN_BASELINE = 20
FLOW_Z = 1.0
OI_LAG = 300
OI_MAX_AGE = 3600

FAMILIES: dict[str, tuple[str, ...]] = {
    "F": ("F_NEGATIVE", "F_BASE", "F_HIGH"),
    "T": ("T_SELLING", "T_NEUTRAL", "T_BUYING"),
    "O": ("O_LONGS_BUILDING", "O_SHORT_COVERING", "O_SHORTS_BUILDING", "O_LONG_LIQUIDATION"),
}
MIN_CANDIDATE_N = 300
MIN_CONFIRM_N = 200
MAX_UNKNOWN_FEATURE = 0.05
MAX_MISSING_OUTCOME = 0.05
MAX_SHORT_MONTHS = 0.05


# ---------------------------------------------------------------------------
# Point-in-time universe
# ---------------------------------------------------------------------------


def month_starts(window: tuple[int, int]) -> list[int]:
    """00:00 UTC on the 1st of every month that starts inside ``window``."""

    d = datetime.fromtimestamp(window[0], tz=timezone.utc)
    year, month = d.year, d.month
    if (d.day, d.hour, d.minute, d.second) != (1, 0, 0, 0):
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    out = []
    while (t := int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())) < window[1]:
        out.append(t)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def decision_bar(market: lr.Market, t: int) -> int | None:
    """Grid index of the 15m bar that closes at ``t`` (None outside the grid)."""

    i = (t - int(market.grid_open[0])) // BAR - 1
    if 0 <= i < market.n_grid and int(market.grid_open[i]) + BAR == t:
        return int(i)
    return None


def first_trade_times(market: lr.Market) -> np.ndarray:
    ok = np.isfinite(market.close)
    first = np.where(ok.any(axis=1), ok.argmax(axis=1), -1)
    return np.where(first >= 0, market.grid_open[np.clip(first, 0, None)], np.iinfo(np.int64).max)


def is_meme(symbol: str) -> bool:
    return symbol.endswith("USDT") and symbol[:-4] in MEMES


def listed_since(market: lr.Market, s: int, start: int, first_trade: np.ndarray, breaks: wm.Breaks) -> int:
    """When pair ``s`` was (re)listed as seen at ``start``: its first trade, or the end of its latest break.

    A break (a hole of more than a day the data resumes from) is a new listing:
    token swaps reuse the ticker (LUNA 2.0 traded as LUNAUSDT from 2022-05-31).
    """

    resumed = [int(market.grid_open[r]) for r in breaks.get(s, ()) if int(market.grid_open[r]) < start]
    return max([int(first_trade[s]), *resumed])


def perp_live(times: np.ndarray | None, start: int) -> bool:
    """A perpetual counts only if it paid funding 30+ days ago and again in the day before ``start``."""

    if times is None or not len(times):
        return False
    a = int(np.searchsorted(times, start, side="left"))
    return int(times[0]) <= start - PERP_SEASON_DAYS * DAY and a > 0 and int(times[a - 1]) >= start - DAY


def monthly_universe(market: lr.Market, start: int, *, perp_of: Mapping[str, str],
                     funding_times: Mapping[str, np.ndarray], first_trade: np.ndarray,
                     breaks: wm.Breaks) -> list[str]:
    """Members for the month opening at ``start`` (docs/MAJORS_STUDY.md), from data visible then."""

    i = decision_bar(market, start)
    lo = None if i is None else i - VOLUME_DAYS * lr.DAY_BARS + 1
    if i is None or lo < 0:
        return []
    window = slice(lo, i + 1)
    volume = np.nansum(market.quote_volume[:, window], axis=1)
    days = np.isfinite(market.close[:, window]).reshape(len(market.symbols), VOLUME_DAYS, lr.DAY_BARS).any(axis=2)
    data_start = int(market.grid_open[0])
    eligible = []
    for s, symbol in enumerate(market.symbols):
        if days[s].sum() < MIN_VOLUME_DAYS:
            continue
        listed = listed_since(market, s, start, first_trade, breaks)
        seasoned = listed <= data_start + DAY or listed <= start - SEASON_DAYS * DAY
        perp_ok = symbol in perp_of and perp_live(funding_times.get(symbol), start)
        if seasoned and perp_ok:
            eligible.append((float(volume[s]), symbol))
    eligible.sort(key=lambda row: (-row[0], row[1]))
    names = [symbol for _, symbol in eligible]
    members = [symbol for symbol in FIXED if symbol in names]
    others = [symbol for symbol in names if symbol not in FIXED]
    top = others[:TOP_OTHERS]
    members += top
    if not any(is_meme(symbol) for symbol in top):
        meme = next((symbol for symbol in others if is_meme(symbol)), None)
        if meme is not None:
            members.append(meme)
    return members


def universes(market: lr.Market, window: tuple[int, int], *, perp_of: Mapping[str, str],
              funding_times: Mapping[str, np.ndarray]) -> dict[int, list[str]]:
    first_trade, breaks = first_trade_times(market), wm.continuity_breaks(market)
    return {m: monthly_universe(market, m, perp_of=perp_of, funding_times=funding_times, first_trade=first_trade,
                                breaks=breaks) for m in month_starts(window)}


# ---------------------------------------------------------------------------
# Features (visible at the decision time t only)
# ---------------------------------------------------------------------------


def funding_feature(times: Sequence[int], rates: Sequence[float], t: int) -> float | None:
    """8h-equivalent mean funding over the 72h before t; None if a payment gap exceeds 8h."""

    lo, hi = t - FUNDING_WINDOW - FUNDING_LAG, t - FUNDING_LAG
    times = np.asarray(times, dtype=np.int64)
    a, b = int(np.searchsorted(times, lo, side="right")), int(np.searchsorted(times, hi, side="right"))
    if b <= a:
        return None
    marks = np.concatenate(([lo], times[a:b], [hi]))
    if np.diff(marks).max() > FUNDING_MAX_GAP:
        return None
    total = float(np.sum(np.asarray(rates, dtype=float)[a:b]))
    return total * 8 * 3600 / FUNDING_WINDOW if math.isfinite(total) else None


def funding_group(rate: float | None) -> str | None:
    if rate is None:
        return None
    return "F_NEGATIVE" if rate < 0 else "F_HIGH" if rate >= FUNDING_HIGH else "F_BASE"


class Flow:
    """24h taker-buy shares from 1h perpetual candles (open_time, quote, taker_buy_quote)."""

    def __init__(self, times: np.ndarray, quote: np.ndarray, taker: np.ndarray):
        self.times = np.asarray(times, dtype=np.int64)
        self.cq = np.concatenate(([0.0], np.cumsum(quote)))
        self.ct = np.concatenate(([0.0], np.cumsum(taker)))

    def share(self, t: int) -> float | None:
        """Share over the 24 candles that closed in (t−24h, t]; None unless all 24 are present."""

        a = int(np.searchsorted(self.times, t - FLOW_HOURS * 3600, side="left"))
        b = int(np.searchsorted(self.times, t - 3600, side="right"))
        if b - a != FLOW_HOURS or self.times[b - 1] - self.times[a] != (FLOW_HOURS - 1) * 3600:
            return None
        quote = self.cq[b] - self.cq[a]
        return (self.ct[b] - self.ct[a]) / quote if quote > 0 else None

    def z(self, t: int) -> float | None:
        now = self.share(t)
        if now is None:
            return None
        base = [v for k in range(1, FLOW_BASELINE_DAYS + 1) if (v := self.share(t - k * DAY)) is not None]
        if len(base) < FLOW_MIN_BASELINE:
            return None
        sd = statistics.stdev(base)
        return (now - statistics.fmean(base)) / sd if sd > 0 else None


def flow_group(z: float | None) -> str | None:
    if z is None:
        return None
    return "T_BUYING" if z >= FLOW_Z else "T_SELLING" if z <= -FLOW_Z else "T_NEUTRAL"


def oi_snapshot(times: np.ndarray, values: np.ndarray, t: int) -> float | None:
    """Latest OI created at least 5 minutes before t, if it is at most an hour old."""

    j = int(np.searchsorted(times, t - OI_LAG, side="right")) - 1
    if j < 0 or t - int(times[j]) > OI_MAX_AGE:
        return None
    return float(values[j])


def oi_group(oi_change: float | None, price_change: float | None) -> str | None:
    if oi_change is None or price_change is None or oi_change == 0 or price_change == 0:
        return None
    if price_change > 0:
        return "O_LONGS_BUILDING" if oi_change > 0 else "O_SHORT_COVERING"
    return "O_SHORTS_BUILDING" if oi_change > 0 else "O_LONG_LIQUIDATION"


# ---------------------------------------------------------------------------
# Coin-day rows
# ---------------------------------------------------------------------------


def outcomes(market: lr.Market, members: Sequence[int], i: int, hours: int,
             breaks: wm.Breaks) -> tuple[np.ndarray, float | None]:
    """Gross % per member (NaN when unknowable) and the equal-weight basket (None below BASKET_MIN)."""

    bars = hours * 3600 // BAR
    returns, _ = lr.forward_returns(market, members, i, bars)
    for k, s in enumerate(members):
        if not wm.continuous(breaks, s, i + 1, i + bars):
            returns[k] = np.nan
    known = returns[np.isfinite(returns)]
    return returns, (float(known.mean()) if len(known) >= BASKET_MIN else None)


def coin_day_rows(market: lr.Market, monthly: Mapping[int, Sequence[str]], window: tuple[int, int], *,
                  funding: Mapping[str, tuple[Sequence[int], Sequence[float]]],
                  taker: Mapping[str, tuple[np.ndarray, np.ndarray, np.ndarray]],
                  oi: Mapping[str, tuple[np.ndarray, np.ndarray]]) -> list[dict[str, Any]]:
    breaks = wm.continuity_breaks(market)
    flows = {spot: Flow(*arrays) for spot, arrays in taker.items()}
    funding = {spot: (np.asarray(times, dtype=np.int64), np.asarray(rates, dtype=float))
               for spot, (times, rates) in funding.items()}
    starts = sorted(monthly)
    rows = []
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else window[1]
        members = [m for m in monthly[start] if m in market.index_of]
        index = [market.index_of[m] for m in members]
        for t in range(max(start, window[0]), min(end, window[1]), DAY):
            i = decision_bar(market, t)
            if i is None:
                continue
            outs = {h: outcomes(market, index, i, h, breaks) for h in HORIZONS_H}
            for n, (symbol, s) in enumerate(zip(members, index)):
                times, rates = funding.get(symbol, ((), ()))
                rate = funding_feature(times, rates, t) if len(times) else None
                z = flows[symbol].z(t) if symbol in flows else None
                oi_change = price_change = None
                if symbol in oi:
                    now, then = oi_snapshot(*oi[symbol], t), oi_snapshot(*oi[symbol], t - DAY)
                    oi_change = None if now is None or then is None else now / then - 1.0
                c_now, c_then = market.close[s, i], (market.close[s, i - lr.DAY_BARS] if i >= lr.DAY_BARS else np.nan)
                if np.isfinite(c_now) and np.isfinite(c_then) and wm.continuous(breaks, s, i - lr.DAY_BARS, i):
                    price_change = float(c_now / c_then - 1.0)
                row = {
                    "symbol": symbol, "decided_at": t, "month": start,
                    "funding_8h": rate, "flow_z": z, "oi_change": oi_change, "price_change_24h": price_change,
                    "F": funding_group(rate), "T": flow_group(z),
                    "O": oi_group(oi_change, price_change) if t >= OI_FROM else None,
                    "in_O": t >= OI_FROM,
                }
                for h, (returns, basket) in outs.items():
                    gross = float(returns[n]) if np.isfinite(returns[n]) else None
                    row[f"gross_{h}"], row[f"basket_{h}"] = gross, basket
                rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Statistics and decisions
# ---------------------------------------------------------------------------


def net(row: Mapping[str, Any], h: int, cost: float = COST_PCT) -> float | None:
    gross = row.get(f"gross_{h}")
    return None if gross is None else gross - cost


def excess(row: Mapping[str, Any], h: int, cost: float = COST_PCT) -> float | None:
    gross, basket = row.get(f"gross_{h}"), row.get(f"basket_{h}")
    return None if gross is None or basket is None else gross - cost - basket


def summarize(pairs: sq.Pairs, *, alpha: float, half_at: int) -> dict[str, Any]:
    values = [v for v, _ in pairs]
    first, second = [v for v, t in pairs if t < half_at], [v for v, t in pairs if t >= half_at]
    return {
        "n": len(values),
        "mean": statistics.fmean(values) if values else None,
        "ci": sq._interval(sq.block_bootstrap_means([pairs])[:, 0], alpha) if len(values) >= 2 else None,
        "h1": statistics.fmean(first) if first else None,
        "h2": statistics.fmean(second) if second else None,
    }


def contrast(a: sq.Pairs, b: sq.Pairs, *, alpha: float, half_at: int) -> dict[str, Any]:
    def mean(pairs, keep=lambda t: True):
        values = [v for v, t in pairs if keep(t)]
        return statistics.fmean(values) if values else None

    def gap(x, y):
        return None if x is None or y is None else x - y

    return {
        "mean": gap(mean(a), mean(b)),
        "ci": sq.diff_ci(a, b, alpha=alpha),
        "h1": gap(mean(a, lambda t: t < half_at), mean(b, lambda t: t < half_at)),
        "h2": gap(mean(a, lambda t: t >= half_at), mean(b, lambda t: t >= half_at)),
    }


def _all_positive(s: Mapping[str, Any]) -> bool:
    ci = s.get("ci")
    return bool(ci) and ci[0] > 0 and (s.get("h1") or 0) > 0 and (s.get("h2") or 0) > 0


def _all_negative(s: Mapping[str, Any]) -> bool:
    ci = s.get("ci")
    return bool(ci) and ci[1] < 0 and (s.get("h1") or 0) < 0 and (s.get("h2") or 0) < 0


def family_rows(rows: Sequence[Mapping[str, Any]], family: str) -> list[Mapping[str, Any]]:
    return [r for r in rows if r["in_O"]] if family == "O" else list(rows)


def evaluate_group(rows: Sequence[Mapping[str, Any]], family: str, group: str, h: int, *,
                   alpha: float, half_at: int) -> dict[str, Any]:
    scope = family_rows(rows, family)
    inside = [r for r in scope if r[family] == group]
    rest = [r for r in scope if r[family] is not None and r[family] != group]
    return {
        "family": family, "group": group, "hours": h,
        "net": summarize(sq._pairs(inside, lambda r: net(r, h)), alpha=alpha, half_at=half_at),
        "excess": summarize(sq._pairs(inside, lambda r: excess(r, h)), alpha=alpha, half_at=half_at),
        "stress_net": summarize(sq._pairs(inside, lambda r: net(r, h, STRESS_COST_PCT)), alpha=alpha,
                                half_at=half_at),
        "minus_rest_excess": contrast(sq._pairs(inside, lambda r: excess(r, h)),
                                      sq._pairs(rest, lambda r: excess(r, h)), alpha=alpha, half_at=half_at),
    }


def completeness(rows: Sequence[Mapping[str, Any]], monthly: Mapping[int, Sequence[str]]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "coin_days": len(rows),
        "short_month_share": (sum(len(m) < MIN_MEMBERS for m in monthly.values()) / len(monthly)) if monthly else 1.0,
    }
    for family in FAMILIES:
        scope = family_rows(rows, family)
        out[f"unknown_{family}"] = (sum(r[family] is None for r in scope) / len(scope)) if scope else 1.0
    for h in HORIZONS_H:
        out[f"missing_outcome_{h}"] = (sum(excess(r, h) is None for r in rows) / len(rows)) if rows else 1.0
    return out


def incomplete(stats: Mapping[str, Any], family: str, h: int) -> list[str]:
    problems = []
    if not stats["coin_days"]:
        problems.append("no coin-days")
    if stats[f"unknown_{family}"] > MAX_UNKNOWN_FEATURE:
        problems.append(f"unknown {family} {stats[f'unknown_{family}']:.1%}")
    if stats[f"missing_outcome_{h}"] > MAX_MISSING_OUTCOME:
        problems.append(f"missing outcome {stats[f'missing_outcome_{h}']:.1%}")
    if stats["short_month_share"] > MAX_SHORT_MONTHS:
        problems.append(f"short months {stats['short_month_share']:.1%}")
    return problems


def candidate_kind(result: Mapping[str, Any]) -> str | None:
    """Discovery rule (docs/MAJORS_STUDY.md): LONG or AVOID candidate, else None."""

    if result["net"]["n"] < MIN_CANDIDATE_N:
        return None
    if _all_positive(result["excess"]) and (result["net"]["mean"] or 0) > 0:
        return "LONG"
    if _all_negative(result["minus_rest_excess"]):
        return "AVOID"
    return None


def select_candidates(results: Sequence[Mapping[str, Any]], limit: int = 2) -> list[dict[str, Any]]:
    """At most ``limit``, one per family, ranked by the distance of the relevant bound from zero."""

    scored = []
    for r in results:
        kind = candidate_kind(r)
        if kind == "LONG":
            scored.append((r["excess"]["ci"][0], kind, r))
        elif kind == "AVOID":
            scored.append((-r["minus_rest_excess"]["ci"][1], kind, r))
    scored.sort(key=lambda row: -row[0])
    chosen, families = [], set()
    for score, kind, r in scored:
        if r["family"] in families:
            continue
        families.add(r["family"])
        chosen.append({"family": r["family"], "group": r["group"], "hours": r["hours"], "kind": kind,
                       "score": score})
        if len(chosen) == limit:
            break
    return chosen


def confirm_verdict(result: Mapping[str, Any], kind: str, problems: Sequence[str]) -> str:
    if problems:
        return "INCOMPLETE_DATA"
    if kind == "AVOID":
        return "KAYBI_AZALTIR" if _all_negative(result["minus_rest_excess"]) else "NO_EFFECT"
    n_ok = result["net"]["n"] >= MIN_CONFIRM_N
    if n_ok and _all_positive(result["net"]) and _all_positive(result["excess"]) \
            and (result["stress_net"]["mean"] or 0) > 0:
        return "PASS"
    if _all_positive(result["excess"]):
        return "SEPETI_YENER"
    ci = result["excess"].get("ci")
    if ci and ci[1] < 0:
        return "TERS"
    return "NO_EFFECT"


# ---------------------------------------------------------------------------
# Pre-registration
# ---------------------------------------------------------------------------


FAMILY = "majors_perp_signals"
FINGERPRINT_FILES = (
    "trading/backtest/majors_signals.py",
    "trading/data/majors_data.py",
    "trading/backtest/weekly_momentum.py",
    "trading/backtest/signal_quality.py",
    "trading/backtest/liquid_replay.py",
    "trading/data/universe_funding.py",
    "trading/data/binance_universe.py",
    "trading/data/binance_vision.py",
    "trading/data/binance_carry_universe.py",
    "trading/data/binance_history_identity.py",
    "acce_unified/liquid_long.py",
    "acce_unified/cex.py",
    "acce_unified/models.py",
)
REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = {"source": "binance_vision_spot_and_um_perpetual", "built_as_of": sq.CONFIRMATION_END,
           "confirmation_window": "2024-09..2026-08"}

# Filled in only after discovery, by the rule in docs/MAJORS_STUDY.md: (family, group, hours, kind).
REGISTERED: tuple[tuple[str, str, int, str], ...] = ()


def code_fingerprint(repo_root: Path = REPO_ROOT) -> str:
    digest = hashlib.sha256()
    for name in FINGERPRINT_FILES:
        digest.update(name.encode())
        digest.update((repo_root / name).read_bytes())
    return digest.hexdigest()[:16]


def trial_params() -> dict[str, Any]:
    return {
        "code_fingerprint": code_fingerprint(),
        "candidates": [f"{g}_{h}h_{k}" for _, g, h, k in REGISTERED],
        "universe": "BTC, ETH + top 10 by 30d spot volume (seasoned 90d, perp 30d) + largest meme; monthly",
        "costs_pct": {"base": COST_PCT, "stress": STRESS_COST_PCT},
        "thresholds": {"funding_high_8h": FUNDING_HIGH, "flow_z": FLOW_Z, "oi_lag_s": OI_LAG,
                       "oi_max_age_s": OI_MAX_AGE},
        "window": list(CONFIRMATION),
        "half_at": HALF_AT,
        "family_alpha": 0.05 / max(1, len(REGISTERED)),
        "bootstrap": {"method": "circular_moving_block_days", "block_days": sq.BLOCK_DAYS, "resamples": 4000},
        "min_coin_days": MIN_CONFIRM_N,
        "completeness": {"max_unknown_feature": MAX_UNKNOWN_FEATURE, "max_missing_outcome": MAX_MISSING_OUTCOME,
                         "max_short_months": MAX_SHORT_MONTHS},
        "verdicts": "LONG: PASS / SEPETI_YENER / TERS / NO_EFFECT; AVOID: KAYBI_AZALTIR / NO_EFFECT; "
                    "INCOMPLETE_DATA gives no verdict",
    }


def trial_id() -> str:
    from trading.research.robustness import trial_id_for

    return trial_id_for(family=FAMILY, params=trial_params(), dataset=DATASET)


def require_registration(registry: Path) -> str:
    from trading.research.robustness import TrialRegistry

    if not REGISTERED:
        raise SystemExit("no candidate is registered; the confirmation window stays sealed")
    tid = trial_id()
    if tid not in {r.trial_id for r in TrialRegistry(registry).selection_trials(FAMILY)}:
        raise SystemExit(f"trial {tid} is not pre-registered for this code; refusing to look at 2024-09+")
    return tid


# ---------------------------------------------------------------------------
# Data assembly and CLI
# ---------------------------------------------------------------------------


def load_inputs(spot_dir: Path, *, end: int):
    from trading.data.universe_funding import load_funding

    market, _, excluded = sq.load_likit_market(spot_dir, end=end)
    funding = load_funding(spot_dir / "funding")
    manifest = json.loads((spot_dir / "funding" / "manifest.json").read_text("utf-8"))
    funding_times = {spot: np.asarray(times, dtype=np.int64) for spot, (times, _) in funding.items()}
    return market, excluded, funding, manifest["perp_of"], funding_times


def build_perp(spot_dir: Path, out_dir: Path, *, window: tuple[int, int], end: int) -> dict:
    from trading.data.majors_data import build_perp_data

    market, _, _, perp_of, funding_times = load_inputs(spot_dir, end=end)
    monthly = universes(market, window, perp_of=perp_of, funding_times=funding_times)
    starts = sorted(monthly)
    month_end = {m: (starts[k + 1] if k + 1 < len(starts) else window[1]) for k, m in enumerate(starts)}
    manifest = build_perp_data(monthly, month_end, perp_of, out_dir, decision_at=end, oi_from=max(OI_FROM, window[0]))
    (out_dir / "universes.json").write_text(json.dumps({str(k): v for k, v in monthly.items()}, indent=1), "utf-8")
    return {"months": len(monthly), "pairs": len(manifest["pairs"])}


def run(spot_dir: Path, perp_dir: Path, *, window: tuple[int, int], end: int, alpha: float,
        half_at: int) -> dict[str, Any]:
    from trading.data.majors_data import load_oi, load_taker

    market, excluded, funding, perp_of, funding_times = load_inputs(spot_dir, end=end)
    monthly = universes(market, window, perp_of=perp_of, funding_times=funding_times)
    rows = coin_day_rows(market, monthly, window, funding=funding, taker=load_taker(perp_dir), oi=load_oi(perp_dir))
    stats = completeness(rows, monthly)
    results = []
    for h in HORIZONS_H:
        always = {"net": summarize(sq._pairs(rows, lambda r: net(r, h)), alpha=alpha, half_at=half_at),
                  "excess": summarize(sq._pairs(rows, lambda r: excess(r, h)), alpha=alpha, half_at=half_at)}
        results.append({"family": "ALL", "group": "ALWAYS_LONG", "hours": h, **always})
        for family, groups in FAMILIES.items():
            for group in groups:
                result = evaluate_group(rows, family, group, h, alpha=alpha, half_at=half_at)
                result["incomplete"] = incomplete(stats, family, h)
                results.append(result)
    return {"excluded": excluded, "monthly": {str(k): v for k, v in monthly.items()}, "completeness": stats,
            "results": results, "rows": rows}


def _fmt(value: Any, spec: str = "+.3f") -> str:
    return "n/a" if value is None else format(value, spec)


def _line(name: str, s: Mapping[str, Any]) -> str:
    ci = "n/a" if not s.get("ci") else f"[{s['ci'][0]:+.3f},{s['ci'][1]:+.3f}]"
    n = f"n={s['n']:>6} " if "n" in s else ""
    return f"    {name:<18} {n}mean={_fmt(s['mean'])} ci={ci} h1={_fmt(s['h1'])} h2={_fmt(s['h2'])}"


def print_report(report: Mapping[str, Any]) -> None:
    print("completeness:", json.dumps(report["completeness"], sort_keys=True))
    for r in report["results"]:
        extra = f"  incomplete={r['incomplete']}" if r.get("incomplete") else ""
        print(f"## {r['family']} {r['group']} {r['hours']}h{extra}" + (f"  -> {r['verdict']}" if "verdict" in r else ""))
        for key in ("net", "excess", "stress_net", "minus_rest_excess"):
            if key in r:
                print(_line(key, r[key]))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str), "utf-8")
    tmp.replace(path)


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse
    import logging

    parser = argparse.ArgumentParser(description="Majors perpetual-signal study (docs/MAJORS_STUDY.md).")
    sub = parser.add_subparsers(dest="mode", required=True)
    build = sub.add_parser("build", help="perp taker/OI data for the sealed discovery window")
    disc = sub.add_parser("discover", help="all families and groups on the sealed discovery window")
    build_c = sub.add_parser("build-confirm", help="confirmation data as of 2026-09-01 (registered trial only)")
    conf = sub.add_parser("confirm", help="the registered candidates, once, on 2024-09..2026-08")
    for p in (build, disc, conf):
        p.add_argument("--spot-dir", type=Path, required=True)
    for p in (build, build_c):
        p.add_argument("--out", type=Path, required=True)
    for p in (disc, conf):
        p.add_argument("--perp-dir", type=Path, required=True)
        p.add_argument("--out", type=Path, required=True)
    for p in (build_c, conf):
        p.add_argument("--trial-registry", type=Path, default=Path("research/trials/registry.jsonl"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    started = time.time()
    if args.mode == "build":
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        print(json.dumps(build_perp(args.spot_dir, args.out, window=DISCOVERY, end=sq.DISCOVERY_END), indent=1))
        return 0
    if args.mode == "build-confirm":
        from trading.data.binance_universe import build_universe
        from trading.data.universe_funding import build_funding

        trial = require_registration(args.trial_registry)
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        spot = args.out / "spot"
        build_universe(spot, lookback_months=sq.CONFIRMATION_MONTHS, decision_at=sq.CONFIRMATION_END)
        build_funding(spot, spot / "funding", decision_at=sq.CONFIRMATION_END)
        summary = build_perp(spot, args.out / "perp", window=CONFIRMATION, end=sq.CONFIRMATION_END)
        print(json.dumps({"trial": trial, **summary}, indent=1))
        return 0
    if args.mode == "discover":
        report = run(args.spot_dir, args.perp_dir, window=DISCOVERY, end=sq.DISCOVERY_END, alpha=0.05,
                     half_at=DISCOVERY_HALF_AT)
        report["candidates"] = select_candidates([r for r in report["results"] if r["family"] != "ALL"
                                                  and not r.get("incomplete")])
        trial = None
    else:
        trial = require_registration(args.trial_registry)
        report = run(args.spot_dir, args.perp_dir, window=CONFIRMATION, end=sq.CONFIRMATION_END,
                     alpha=0.05 / len(REGISTERED), half_at=HALF_AT)
        registered = {(f, g, h): k for f, g, h, k in REGISTERED}
        kept = []
        for r in report["results"]:
            key = (r["family"], r["group"], r["hours"])
            if key in registered:
                r["kind"] = registered[key]
                r["verdict"] = confirm_verdict(r, registered[key], r["incomplete"])
                kept.append(r)
        report["results"] = kept
    rows = report.pop("rows")
    print(f"excluded (history identity): {json.dumps(report['excluded'], sort_keys=True)}")
    print(f"{args.mode}: {len(rows)} coin-days, {len(report['monthly'])} months"
          + (f", trial {trial} (pre-registered)" if trial else ""))
    print_report(report)
    if "candidates" in report:
        print("candidates (rule in docs/MAJORS_STUDY.md):", json.dumps(report["candidates"]))
    _write_json(args.out, {"mode": args.mode, "trial": trial, **report, "can_authorize_trade": False})
    print(f"wrote {args.out} in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
