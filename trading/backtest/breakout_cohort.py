"""Breakout cohort: what was observable before coins that went 2x? Research only (docs/BREAKOUT_COHORT.md).

For every (coin, cohort origin) in the survivorship-free Binance USDT spot
universe: did the daily close reach 2x within 12 months, what was knowable
at the origin, and what did the path look like. Inference uses the
non-overlapping annual cohorts (origins on 1 September; discovery
2018..2021, confirmation 2022..2025, sealed until registered); monthly
cohorts are descriptive only. Coin indicators are compared *within* a cohort
(lift = P(2x | top bucket) - P(2x | bottom bucket)), pooled across cohorts
with a stratified bootstrap over coins; market, macro and perpetual layers
are reported, never candidates. Every bucket threshold is fixed here before
any run.

Point in time: features read only bars closed before the origin; labels
read only bars from the origin on; a hole longer than one day inside a
window (token swap, halt) makes the observation unknown, never a return;
a symbol whose data stops before the window ends is a delisting and exits
at its last close. Missing data is unknown, never zero (AGENTS.md §1, §2).

No order authority (AGENTS.md §4).
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

DAY = 86_400
WEEK_DAYS = 7
SCHEMA = "breakout-cohort/v1"
FAMILY = "breakout_cohort_spot"

# ---------------------------------------------------------------------------
# Protocol constants (docs/BREAKOUT_COHORT.md §2-§6); a change is a new trial
# ---------------------------------------------------------------------------

LABEL_DAYS = 365                      # primary label horizon
LONG_LABEL_DAYS = 730                 # descriptive horizon
ORIGIN_MONTH = 9                      # annual inference cohorts start on 1 September
DISCOVERY_YEARS = (2018, 2019, 2020, 2021)
CONFIRMATION_YEARS = (2022, 2023, 2024, 2025)
MONTHLY_FROM = (2017, 11)             # descriptive monthly cohorts
DEFAULT_DATA_END = (2026, 9)          # exclusive: the first day not covered (last full month 2026-08)
MIN_HISTORY_DAYS = 90
MIN_MEDIAN_VOLUME = 1_000_000.0
MIN_VOLUME_DAYS = 25                  # of the last 30
UNIVERSE_TOP = 150
MAX_MISSING = {90: 2, 365: 5, 730: 10}  # single missing days tolerated per window length (holes of 2+ days break)
MIN_BUCKET = 10                       # coins per bucket for a within-cohort lift
MIN_COHORTS = 3
MIN_LIFT = 0.15
MAX_CANDIDATES = 5
BOOTSTRAP_DRAWS = 2000
RANDOM_REPS = 1000
ALPHA = 0.05
BTC = "BTCUSDT"
DELIST_GRACE_DAYS = 7

COIN_INDICATORS = ("R90", "RS90", "DIST_HIGH365", "DIST_LOW730", "VOL_TREND", "SIGMA90", "AGE", "WICK90",
                   "FLASH90", "R365", "VOL_RANK")
PERP_INDICATORS = ("FUND90", "PWICK90", "PFLASH90")
MARKET_INDICATORS = ("BTC_TREND", "BTC_R90", "BTC_DIST_ATH", "BREADTH90", "BREADTH_POS", "STABLE_90", "FNG30",
                     "UNIVERSE_SIZE")
MACRO_INDICATORS = ("REAL_10Y", "REAL_10Y_90", "NET_LIQ_90", "VIX", "NASDAQ_90")


@dataclass(frozen=True)
class Bucket:
    low: float
    high: float
    reverse: bool = False             # True: a small value is the "top" bucket (ranks)

    def of(self, value: float | None) -> int | None:
        if value is None or not math.isfinite(value):
            return None
        if self.reverse:
            return 2 if value <= self.low else (0 if value > self.high else 1)
        return 0 if value < self.low else (2 if value > self.high else 1)


BUCKETS: dict[str, Bucket] = {
    "R90": Bucket(-0.30, 0.30), "RS90": Bucket(-20.0, 20.0), "DIST_HIGH365": Bucket(-0.70, -0.30),
    "DIST_LOW730": Bucket(0.50, 2.00), "VOL_TREND": Bucket(0.8, 1.25), "SIGMA90": Bucket(0.8, 1.3),
    "AGE": Bucket(180, 730), "WICK90": Bucket(-0.40, -0.20), "FLASH90": Bucket(-0.25, -0.10),
    "R365": Bucket(-0.50, 0.50), "VOL_RANK": Bucket(30, 90, reverse=True),
    "FUND90": Bucket(0.0, 0.10), "PWICK90": Bucket(-0.40, -0.20), "PFLASH90": Bucket(-0.25, -0.10),
    "BTC_TREND": Bucket(0.5, 1.5), "BTC_R90": Bucket(-0.20, 0.20), "BTC_DIST_ATH": Bucket(-0.50, -0.20),
    "BREADTH90": Bucket(0.33, 0.66), "BREADTH_POS": Bucket(0.33, 0.66), "STABLE_90": Bucket(0.0, 0.05),
    "FNG30": Bucket(30, 60),
}
TREND_CODES = {"DOWN": 0, "SIDEWAYS": 1, "UP": 2}


def day_of(year: int, month: int, day: int = 1) -> int:
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp()) // DAY


def iso(day: int) -> str:
    return date.fromtimestamp(day * DAY).isoformat()


def annual_origins(years: Iterable[int]) -> list[int]:
    return [day_of(y, ORIGIN_MONTH) for y in years]


def monthly_origins(data_end_day: int, horizon: int = LABEL_DAYS) -> list[int]:
    year, month = MONTHLY_FROM
    out = []
    while True:
        day = day_of(year, month)
        if day + horizon > data_end_day:
            return out
        out.append(day)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


SEAL_DAY = annual_origins(CONFIRMATION_YEARS)[0]


def purge_satisfied() -> bool:
    """No discovery label path reaches the first confirmation origin."""

    return annual_origins(DISCOVERY_YEARS)[-1] + LABEL_DAYS <= SEAL_DAY


# ---------------------------------------------------------------------------
# Dense daily series
# ---------------------------------------------------------------------------


@dataclass
class Series:
    """One symbol's daily bars on a dense day grid; NaN where a day is missing."""

    symbol: str
    day0: int
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    qv: np.ndarray

    @property
    def last_day(self) -> int:
        finite = np.nonzero(np.isfinite(self.close))[0]
        return self.day0 + int(finite[-1]) if len(finite) else self.day0 - 1

    def slice(self, name: str, start: int, end: int) -> np.ndarray:
        """Values for days [start, end); NaN outside the stored range."""

        arr = getattr(self, name)
        out = np.full(max(0, end - start), np.nan)
        lo, hi = max(start, self.day0), min(end, self.day0 + len(arr))
        if hi > lo:
            out[lo - start:hi - start] = arr[lo - self.day0:hi - self.day0]
        return out

    def value(self, name: str, day: int) -> float:
        idx = day - self.day0
        arr = getattr(self, name)
        return float(arr[idx]) if 0 <= idx < len(arr) else math.nan


def series_from_rows(symbol: str, rows: Iterable[Sequence[float]]) -> Series:
    """Rows (day, open, high, low, close, quote_volume); a duplicate day keeps its first row."""

    seen: dict[int, tuple[float, ...]] = {}
    for row in rows:
        day = int(row[0])
        if day not in seen:
            seen[day] = tuple(float(x) for x in row[1:6])
    if not seen:
        raise ValueError(f"{symbol}: no rows")
    day0, day1 = min(seen), max(seen)
    n = day1 - day0 + 1
    cols = [np.full(n, np.nan) for _ in range(5)]
    for day, values in seen.items():
        for k in range(5):
            cols[k][day - day0] = values[k]
    return Series(symbol, day0, *cols)


def _holes(values: np.ndarray) -> tuple[int, bool]:
    """(missing count, has a hole of two or more consecutive missing days)."""

    missing = ~np.isfinite(values)
    if not missing.any():
        return 0, False
    run = 0
    for m in missing:
        run = run + 1 if m else 0
        if run >= 2:
            return int(missing.sum()), True
    return int(missing.sum()), False


def _window(series: Series, name: str, start: int, end: int) -> np.ndarray | None:
    """Forward-filled values for [start, end), or None when the window has a hole or too many gaps."""

    values = series.slice(name, start, end)
    length = end - start
    allowed = MAX_MISSING.get(length, max(2, length // 45))
    if not math.isfinite(values[0]) or not math.isfinite(values[-1]):
        return None
    missing, hole = _holes(values)
    if hole or missing > allowed:
        return None
    out = values.copy()
    for k in range(1, len(out)):
        if not math.isfinite(out[k]):
            out[k] = out[k - 1]
    return out


# ---------------------------------------------------------------------------
# Membership and coin features at an origin (history = days < origin)
# ---------------------------------------------------------------------------


def median_volume(series: Series, origin: int) -> float | None:
    qv = series.slice("qv", origin - 30, origin)
    finite = qv[np.isfinite(qv)]
    return float(np.median(finite)) if len(finite) >= MIN_VOLUME_DAYS else None


def eligible(series: Series, origin: int) -> bool:
    if series.day0 > origin - MIN_HISTORY_DAYS:
        return False
    if not math.isfinite(series.value("close", origin - 1)):
        return False
    history = series.slice("close", series.day0, origin)
    return int(np.isfinite(history).sum()) >= MIN_HISTORY_DAYS


def members_at(universe: Mapping[str, Series], origin: int) -> list[tuple[str, float]]:
    """(symbol, 30-day median quote volume) of the top UNIVERSE_TOP eligible symbols, largest first."""

    rows = []
    for symbol, series in universe.items():
        if not eligible(series, origin):
            continue
        volume = median_volume(series, origin)
        if volume is None or volume < MIN_MEDIAN_VOLUME:
            continue
        rows.append((symbol, volume))
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows[:UNIVERSE_TOP]


def _ratio(a: float, b: float) -> float | None:
    return float(a / b - 1.0) if math.isfinite(a) and math.isfinite(b) and b > 0 else None


def coin_features(series: Series, origin: int) -> dict[str, float | None]:
    last = series.value("close", origin - 1)
    out: dict[str, float | None] = {}
    w91 = _window(series, "close", origin - 91, origin)
    out["R90"] = _ratio(last, w91[0]) if w91 is not None else None
    w366 = _window(series, "close", origin - 366, origin)
    out["R365"] = _ratio(last, w366[0]) if w366 is not None else None
    # distance from the 365-day high (or the shorter available history, at least 90 days)
    start = max(series.day0, origin - 365)
    w = _window(series, "close", start, origin) if origin - start >= MIN_HISTORY_DAYS else None
    out["DIST_HIGH365"] = _ratio(last, float(np.max(w))) if w is not None else None
    start = max(series.day0, origin - 730)
    w = _window(series, "close", start, origin) if origin - start >= 180 else None
    out["DIST_LOW730"] = _ratio(last, float(np.min(w))) if w is not None else None
    qv30, qv90 = series.slice("qv", origin - 30, origin), series.slice("qv", origin - 90, origin)
    f30, f90 = qv30[np.isfinite(qv30)], qv90[np.isfinite(qv90)]
    out["VOL_TREND"] = (float(np.median(f30) / np.median(f90))
                        if len(f30) >= MIN_VOLUME_DAYS and len(f90) >= 75 and np.median(f90) > 0 else None)
    if w91 is not None:
        returns = np.diff(np.log(w91))
        out["SIGMA90"] = float(np.std(returns, ddof=1) * math.sqrt(365)) if len(returns) >= 60 else None
    else:
        out["SIGMA90"] = None
    out["AGE"] = float(origin - series.day0)
    lows = _window(series, "low", origin - 90, origin)
    out["WICK90"] = _ratio(float(np.min(lows)), last) if lows is not None else None
    out["FLASH90"] = flash_depth(series, origin - 90, origin)
    return out


def flash_depth(series: Series, start: int, end: int, *, entry: float | None = None) -> float | None:
    """Deepest single-day wick in [start, end): min(low / previous close) - 1; ``entry`` stands in for the
    previous close of the first day when given."""

    lows = series.slice("low", start, end)
    closes = series.slice("close", start - 1, end - 1)
    if entry is not None and len(closes):
        closes = closes.copy()
        closes[0] = entry
    ok = np.isfinite(lows) & np.isfinite(closes) & (closes > 0)
    if not ok.any():
        return None
    return float(np.min(lows[ok] / closes[ok]) - 1.0)


# ---------------------------------------------------------------------------
# Labels at an origin (window = days [origin, origin + horizon))
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Label:
    horizon: int
    entry: float
    multiple: float               # max close / entry
    hit: bool                     # multiple >= 2
    t2x: int | None               # days from the origin to the first close >= 2x
    dd_before_2x: float           # closes vs the running peak (entry included) up to t2x, or the whole window
    dd_max: float
    wick_min: float               # min low / entry - 1
    flash_max: float              # deepest single-day wick inside the window
    final_return: float           # last close in the window / entry - 1
    delisted: bool
    days: int                     # trading days with data inside the window

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def label_at(series: Series, origin: int, horizon: int, *, data_end_day: int) -> Label | None:
    """None when the window is not fully inside the data, the entry is missing or the path has a hole."""

    end = origin + horizon
    if end > data_end_day:
        return None
    entry = series.value("open", origin)
    if not math.isfinite(entry) or entry <= 0:
        return None
    closes = series.slice("close", origin, end)
    lows = series.slice("low", origin, end)
    finite = np.nonzero(np.isfinite(closes))[0]
    if not len(finite) or finite[0] != 0:
        return None
    last = int(finite[-1])
    inside = closes[:last + 1]
    missing, hole = _holes(inside)
    if hole or missing > MAX_MISSING.get(horizon, horizon // 45):
        return None                                       # a break inside the window: unknown, never a return
    delisted = last < horizon - 1 - DELIST_GRACE_DAYS and series.last_day <= origin + last
    if last < horizon - 1 and not delisted:
        return None                                       # data stops early but resumes later: unknown
    path = inside.copy()
    for k in range(1, len(path)):
        if not math.isfinite(path[k]):
            path[k] = path[k - 1]
    target = 2.0 * entry
    hits = np.nonzero(path >= target)[0]
    t2x = int(hits[0]) if len(hits) else None
    peak = np.maximum.accumulate(np.concatenate([[entry], path]))[1:]
    drawdown = path / peak - 1.0
    before = drawdown[:t2x] if t2x is not None else drawdown
    dd_before = float(np.min(before)) if len(before) else 0.0
    low_path = lows[:last + 1]
    low_path = low_path[np.isfinite(low_path)]
    previous = series.value("close", origin - 1)
    return Label(
        horizon=horizon, entry=float(entry), multiple=float(np.max(path) / entry), hit=bool(len(hits)), t2x=t2x,
        dd_before_2x=dd_before, dd_max=float(np.min(drawdown)),
        wick_min=float(np.min(low_path) / entry - 1.0) if len(low_path) else math.nan,
        flash_max=flash_depth(series, origin, origin + last + 1,
                              entry=previous if math.isfinite(previous) else float(entry)) or math.nan,
        final_return=float(path[-1] / entry - 1.0), delisted=bool(delisted), days=int(np.isfinite(inside).sum()),
    )


# ---------------------------------------------------------------------------
# Market, perp and macro layers
# ---------------------------------------------------------------------------


def btc_trend(btc: Series, origin: int) -> str | None:
    """Weekly-channel state of BTC at the last completed week (weeks end on Monday 00:00 UTC)."""

    weekday = (origin + 3) % 7                            # 1970-01-01 was a Thursday; Monday -> 0
    monday = origin - weekday
    closes = [btc.value("close", monday - 1 - 7 * k) for k in range(11)]
    if any(not math.isfinite(c) for c in closes):
        return None
    last, previous = closes[0], closes[1:]
    return "UP" if last > max(previous) else ("DOWN" if last < min(previous) else "SIDEWAYS")


def market_features(btc: Series, origin: int, member_r90: Sequence[float | None]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    trend = btc_trend(btc, origin)
    out["BTC_TREND"] = trend
    btc_feats = coin_features(btc, origin)
    out["BTC_R90"] = btc_feats["R90"]
    history = btc.slice("close", btc.day0, origin)
    history = history[np.isfinite(history)]
    last = btc.value("close", origin - 1)
    out["BTC_DIST_ATH"] = _ratio(last, float(np.max(history))) if len(history) else None
    known = [r for r in member_r90 if r is not None]
    if known and out["BTC_R90"] is not None:
        out["BREADTH90"] = sum(r > out["BTC_R90"] for r in known) / len(known)
    else:
        out["BREADTH90"] = None
    out["BREADTH_POS"] = sum(r > 0 for r in known) / len(known) if known else None
    return out


def value_on_or_before(table: Mapping[int, float], day: int, *, max_stale: int = 7) -> float | None:
    for back in range(max_stale + 1):
        value = table.get(day - back)
        if value is not None and math.isfinite(value):
            return float(value)
    return None


def stable_change(table: Mapping[int, float], origin: int) -> float | None:
    now, then = value_on_or_before(table, origin - 1), value_on_or_before(table, origin - 91)
    return _ratio(now, then) if now is not None and then is not None else None


def fng_mean(table: Mapping[int, float], origin: int) -> float | None:
    values = [table[d] for d in range(origin - 30, origin) if d in table and math.isfinite(table[d])]
    return float(statistics.fmean(values)) if len(values) >= 20 else None


def funding_90(rates: Sequence[tuple[int, float]] | None, origin: int) -> float | None:
    """Annualised mean funding of the settlements in the 90 days before the origin (8h settlements)."""

    if not rates:
        return None
    start, end = (origin - 90) * DAY, origin * DAY
    window = [r for t, r in rates if start <= t < end]
    return float(statistics.fmean(window) * 3 * 365) if len(window) >= 200 else None


def macro_features(monthly: Mapping[str, Mapping[str, Any]], daily: Mapping[str, Mapping[str, Any]],
                   origin: int) -> dict[str, float | None]:
    """Previous month-end values (descriptive) and the daily point-in-time rows at the origin."""

    d = date.fromtimestamp(origin * DAY)
    prev = f"{d.year - 1:04d}-12" if d.month == 1 else f"{d.year:04d}-{d.month - 1:02d}"
    y, m = (d.year, d.month - 4) if d.month > 4 else (d.year - 1, d.month + 8)
    prev3 = f"{y:04d}-{m:02d}"
    row, row3 = monthly.get(prev) or {}, monthly.get(prev3) or {}

    def num(r: Mapping[str, Any], key: str) -> float | None:
        v = r.get(key)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) else None

    real, real3 = num(row, "us_10y_real"), num(row3, "us_10y_real")
    liq, liq3 = num(row, "net_liquidity_busd"), num(row3, "net_liquidity_busd")
    today, back = daily.get(iso(origin)) or {}, daily.get(iso(origin - 90)) or {}
    nasdaq, nasdaq_back = num(today, "nasdaq"), num(back, "nasdaq")
    return {
        "REAL_10Y": real,
        "REAL_10Y_90": (real - real3) if real is not None and real3 is not None else None,
        "NET_LIQ_90": _ratio(liq, liq3) if liq is not None and liq3 is not None else None,
        "VIX": num(today, "vix"),
        "NASDAQ_90": _ratio(nasdaq, nasdaq_back) if nasdaq is not None and nasdaq_back is not None else None,
    }


# ---------------------------------------------------------------------------
# Dataset and cohorts
# ---------------------------------------------------------------------------


@dataclass
class Dataset:
    spot: dict[str, Series]
    data_end_day: int
    perp: dict[str, Series] = field(default_factory=dict)          # keyed by spot symbol
    funding: dict[str, list[tuple[int, float]]] = field(default_factory=dict)
    stablecoins: dict[int, float] = field(default_factory=dict)    # day -> total circulating USD
    fng: dict[int, float] = field(default_factory=dict)            # day -> index value
    macro_monthly: dict[str, dict[str, Any]] = field(default_factory=dict)
    macro_daily: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class Cohort:
    origin: int
    kind: str                                   # "annual" or "monthly"
    rows: list[dict[str, Any]]                  # one per member: symbol, features, label, label24, buckets
    market: dict[str, Any]
    macro: dict[str, Any]

    @property
    def name(self) -> str:
        return iso(self.origin)


def build_cohort(data: Dataset, origin: int, kind: str) -> Cohort:
    members = members_at(data.spot, origin)
    rows: list[dict[str, Any]] = []
    for rank, (symbol, volume) in enumerate(members, 1):
        series = data.spot[symbol]
        feats = coin_features(series, origin)
        feats["VOL_RANK"] = float(rank)
        perp = data.perp.get(symbol)
        perp_feats: dict[str, float | None] = {"FUND90": funding_90(data.funding.get(symbol), origin),
                                               "PWICK90": None, "PFLASH90": None}
        if perp is not None:
            plow = _window(perp, "low", origin - 90, origin)
            plast = perp.value("close", origin - 1)
            perp_feats["PWICK90"] = _ratio(float(np.min(plow)), plast) if plow is not None else None
            perp_feats["PFLASH90"] = flash_depth(perp, origin - 90, origin)
        label = label_at(series, origin, LABEL_DAYS, data_end_day=data.data_end_day)
        label24 = label_at(series, origin, LONG_LABEL_DAYS, data_end_day=data.data_end_day)
        plabel = (label_at(perp, origin, LABEL_DAYS, data_end_day=data.data_end_day) if perp is not None else None)
        rows.append({
            "symbol": symbol, "volume30": volume, "features": feats, "perp": perp_feats,
            "label": label.to_dict() if label else None, "label24": label24.to_dict() if label24 else None,
            "perp_label": plabel.to_dict() if plabel else None,
        })
    btc = data.spot.get(BTC)
    member_r90 = [r["features"]["R90"] for r in rows]
    for row in rows:
        feats = row["features"]
        if btc is not None:
            btc_r90 = coin_features(btc, origin)["R90"]
            r90 = feats["R90"]
            feats["RS90"] = (r90 - btc_r90) * 100.0 if r90 is not None and btc_r90 is not None else None
        else:
            feats["RS90"] = None
        row["buckets"] = {name: BUCKETS[name].of(feats.get(name)) for name in COIN_INDICATORS}
        row["buckets"].update({name: BUCKETS[name].of(row["perp"].get(name)) for name in PERP_INDICATORS})
    market = market_features(btc, origin, member_r90) if btc is not None else {k: None for k in MARKET_INDICATORS}
    market["STABLE_90"] = stable_change(data.stablecoins, origin)
    market["FNG30"] = fng_mean(data.fng, origin)
    market["UNIVERSE_SIZE"] = len(rows)
    macro = macro_features(data.macro_monthly, data.macro_daily, origin)
    return Cohort(origin, kind, rows, market, macro)


def inference_cohorts(data: Dataset, years: Iterable[int]) -> list[Cohort]:
    return [build_cohort(data, origin, "annual") for origin in annual_origins(years)]


def descriptive_cohorts(data: Dataset, *, before: int | None = None) -> list[Cohort]:
    origins = [o for o in monthly_origins(data.data_end_day) if before is None or o < before]
    return [build_cohort(data, origin, "monthly") for origin in origins]


# ---------------------------------------------------------------------------
# Statistics (docs/BREAKOUT_COHORT.md §6)
# ---------------------------------------------------------------------------


def _labelled(cohort: Cohort) -> list[dict[str, Any]]:
    return [r for r in cohort.rows if r["label"] is not None]


def base_rate(cohort: Cohort) -> dict[str, Any]:
    rows = _labelled(cohort)
    hits = sum(r["label"]["hit"] for r in rows)
    rows24 = [r for r in cohort.rows if r["label24"] is not None]
    return {
        "members": len(cohort.rows), "labelled": len(rows), "unknown": len(cohort.rows) - len(rows),
        "hit_rate_12m": hits / len(rows) if rows else None,
        "hit_rate_24m": (sum(r["label24"]["hit"] for r in rows24) / len(rows24)) if rows24 else None,
        "delisted": sum(r["label"]["delisted"] for r in rows),
    }


def cohort_lift(rows: Sequence[Mapping[str, Any]], indicator: str) -> dict[str, Any]:
    """Within one cohort: P(hit | top bucket) - P(hit | bottom bucket); None below MIN_BUCKET coins."""

    top = [r for r in rows if r["label"] is not None and r["buckets"].get(indicator) == 2]
    bottom = [r for r in rows if r["label"] is not None and r["buckets"].get(indicator) == 0]
    known = len(top) >= MIN_BUCKET and len(bottom) >= MIN_BUCKET
    p_top = sum(r["label"]["hit"] for r in top) / len(top) if top else None
    p_bottom = sum(r["label"]["hit"] for r in bottom) / len(bottom) if bottom else None
    return {"n_top": len(top), "n_bottom": len(bottom), "p_top": p_top, "p_bottom": p_bottom,
            "lift": (p_top - p_bottom) if known else None}


def pooled_lift(cohorts: Sequence[Cohort], indicator: str) -> dict[str, Any]:
    per = {c.name: cohort_lift(c.rows, indicator) for c in cohorts}
    lifts = [v["lift"] for v in per.values() if v["lift"] is not None]
    return {"per_cohort": per, "known": len(lifts), "lift": statistics.fmean(lifts) if lifts else None,
            "signs_agree": bool(lifts) and (all(l > 0 for l in lifts) or all(l < 0 for l in lifts))}


def stratified_bootstrap(cohorts: Sequence[Cohort], indicator: str, *, draws: int = BOOTSTRAP_DRAWS,
                         seed: int = 0, alpha: float = ALPHA) -> tuple[float, float] | None:
    """Resample coins within each cohort (cohorts are never resampled); CI of the pooled lift."""

    rng = np.random.default_rng(seed)
    groups = []
    for c in cohorts:
        top = np.array([r["label"]["hit"] for r in c.rows if r["label"] and r["buckets"].get(indicator) == 2], float)
        bottom = np.array([r["label"]["hit"] for r in c.rows if r["label"] and r["buckets"].get(indicator) == 0],
                          float)
        if len(top) >= MIN_BUCKET and len(bottom) >= MIN_BUCKET:
            groups.append((top, bottom))
    if not groups:
        return None
    out = np.empty(draws)
    for d in range(draws):
        lifts = []
        for top, bottom in groups:
            t = top[rng.integers(0, len(top), len(top))].mean()
            b = bottom[rng.integers(0, len(bottom), len(bottom))].mean()
            lifts.append(t - b)
        out[d] = float(np.mean(lifts))
    return float(np.quantile(out, alpha / 2)), float(np.quantile(out, 1 - alpha / 2))


def indicator_stats(cohorts: Sequence[Cohort], indicator: str, *, alpha: float = ALPHA, seed: int = 0) -> dict:
    pooled = pooled_lift(cohorts, indicator)
    ci = stratified_bootstrap(cohorts, indicator, alpha=alpha, seed=seed)
    return {**pooled, "ci": list(ci) if ci else None, "alpha": alpha}


def is_candidate(stats: Mapping[str, Any]) -> bool:
    lift, ci = stats.get("lift"), stats.get("ci")
    return (stats.get("known", 0) >= MIN_COHORTS and bool(stats.get("signs_agree")) and lift is not None
            and abs(lift) >= MIN_LIFT and ci is not None and (ci[0] > 0 or ci[1] < 0))


def select_candidates(all_stats: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The protocol's candidate rule over the coin indicators only; at most MAX_CANDIDATES, by lift / width."""

    out = []
    for name in COIN_INDICATORS:
        stats = all_stats.get(name) or {}
        if is_candidate(stats):
            width = stats["ci"][1] - stats["ci"][0]
            out.append({"indicator": name, "direction": "UP" if stats["lift"] > 0 else "DOWN",
                        "lift": stats["lift"], "ci": stats["ci"], "score": abs(stats["lift"]) / max(width, 1e-9)})
    out.sort(key=lambda c: (-c["score"], c["indicator"]))
    return out[:MAX_CANDIDATES]


def random_control(cohorts: Sequence[Cohort], *, reps: int = RANDOM_REPS, seed: int = 0) -> dict[str, Any]:
    """How often a random three-way bucket satisfies the candidate rule (the method's own noise)."""

    rng = np.random.default_rng(seed)
    passes = 0
    for rep in range(reps):
        for c in cohorts:
            for r in c.rows:
                r["buckets"]["_RANDOM"] = int(rng.integers(0, 3))
        stats = indicator_stats(cohorts, "_RANDOM", seed=rep)
        passes += is_candidate(stats)
    for c in cohorts:
        for r in c.rows:
            r["buckets"].pop("_RANDOM", None)
    return {"reps": reps, "passes": passes, "rate": passes / reps if reps else None}


def verdict(discovery: Mapping[str, Any], confirmation: Mapping[str, Any], *, family_alpha: float) -> str:
    lifts = [v["lift"] for v in confirmation["per_cohort"].values() if v["lift"] is not None]
    unknown_share = confirmation.get("unknown_share") or 0.0
    if len(lifts) < MIN_COHORTS or unknown_share > 0.05:
        return "INCOMPLETE_DATA"
    sign = 1 if discovery["lift"] > 0 else -1
    agree = sum((l > 0) == (sign > 0) for l in lifts)
    ci, lift = confirmation.get("ci"), confirmation.get("lift")
    if ci is None or lift is None:
        return "INCOMPLETE_DATA"
    excludes = ci[0] > 0 or ci[1] < 0
    if excludes and lift * sign > 0 and agree >= MIN_COHORTS:
        return "CONFIRMED"
    if excludes and lift * sign < 0:
        return "REVERSED"
    return "NO_EFFECT"


# ---------------------------------------------------------------------------
# Descriptive outputs: period cards and path tables
# ---------------------------------------------------------------------------


def _quantiles(values: Sequence[float]) -> dict[str, float | None]:
    finite = [v for v in values if v is not None and math.isfinite(v)]
    if not finite:
        return {"n": 0, "median": None, "q25": None, "q75": None, "worst5": None}
    arr = np.array(finite)
    return {"n": len(arr), "median": float(np.median(arr)), "q25": float(np.quantile(arr, 0.25)),
            "q75": float(np.quantile(arr, 0.75)), "worst5": float(np.quantile(arr, 0.05))}


def path_stats(rows: Sequence[Mapping[str, Any]], key: str = "label") -> dict[str, Any]:
    labelled = [r[key] for r in rows if r.get(key)]
    hits = [l for l in labelled if l["hit"]]
    return {
        "n": len(labelled), "hits": len(hits),
        "t2x_days": _quantiles([l["t2x"] for l in hits]),
        "dd_before_2x": _quantiles([l["dd_before_2x"] for l in hits]),
        "wick_min_hits": _quantiles([l["wick_min"] for l in hits]),
        "dd_max": _quantiles([l["dd_max"] for l in labelled]),
        "wick_min": _quantiles([l["wick_min"] for l in labelled]),
        "flash_max": _quantiles([l["flash_max"] for l in labelled]),
    }


def period_card(cohort: Cohort) -> dict[str, Any]:
    return {"origin": cohort.name, "kind": cohort.kind, "base": base_rate(cohort), "market": cohort.market,
            "macro": cohort.macro, "path": path_stats(cohort.rows)}


def sigma_wick_table(cohorts: Sequence[Cohort]) -> dict[str, Any]:
    """WICK_MIN, FLASH_MAX and DD_MAX distributions by SIGMA90 bucket (spot; perp where a perp existed)."""

    out: dict[str, Any] = {}
    for b in (0, 1, 2):
        rows = [r for c in cohorts for r in c.rows if r["buckets"].get("SIGMA90") == b]
        out[str(b)] = {"spot": path_stats(rows), "perp": path_stats(rows, "perp_label")}
    return out


# ---------------------------------------------------------------------------
# Pre-registration and seal
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[2]
FINGERPRINT_FILES = (
    "trading/backtest/breakout_cohort.py",
    "trading/backtest/market_data.py",
    "trading/data/binance_universe.py",
    "trading/data/binance_vision.py",
    "trading/data/binance_carry_universe.py",
    "trading/data/binance_history_identity.py",
    "acce_unified/cex.py",
)


def code_fingerprint(repo_root: Path = REPO_ROOT) -> str:
    digest = hashlib.sha256()
    for name in FINGERPRINT_FILES:
        digest.update(name.encode())
        digest.update((repo_root / name).read_bytes())
    return digest.hexdigest()[:16]


def dataset_description(data_end: tuple[int, int] = DEFAULT_DATA_END) -> dict[str, Any]:
    return {"source": "binance_vision_spot_1d; um perp 1d and funding (descriptive); defillama stablecoins "
                      "(current vintage, descriptive); alternative.me fng (descriptive); research/atlas (descriptive)",
            "built_as_of": day_of(*data_end) * DAY,
            "confirmation": f"annual cohorts {CONFIRMATION_YEARS[0]}-09..{CONFIRMATION_YEARS[-1]}-09, "
                            f"{LABEL_DAYS}-day labels"}


def trial_params(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "code_fingerprint": code_fingerprint(),
        "candidates": [{"indicator": c["indicator"], "direction": c["direction"]} for c in candidates],
        "buckets": {name: [BUCKETS[name].low, BUCKETS[name].high] for name in COIN_INDICATORS},
        "label_days": LABEL_DAYS, "origin_month": ORIGIN_MONTH,
        "discovery_years": list(DISCOVERY_YEARS), "confirmation_years": list(CONFIRMATION_YEARS),
        "membership": {"min_history_days": MIN_HISTORY_DAYS, "min_median_volume": MIN_MEDIAN_VOLUME,
                       "top": UNIVERSE_TOP},
        "rule": {"min_bucket": MIN_BUCKET, "min_cohorts": MIN_COHORTS, "min_lift": MIN_LIFT,
                 "max_candidates": MAX_CANDIDATES, "alpha": ALPHA,
                 "family_alpha": ALPHA / max(1, len(candidates))},
        "bootstrap": {"method": "stratified_over_coins_within_cohort", "draws": BOOTSTRAP_DRAWS, "seed": 0},
        "verdicts": "CONFIRMED / NO_EFFECT / REVERSED / INCOMPLETE_DATA",
        "eligible": list(COIN_INDICATORS),
        "descriptive_only": list(PERP_INDICATORS + MARKET_INDICATORS + MACRO_INDICATORS),
    }


def trial_id(candidates: Sequence[Mapping[str, Any]], data_end: tuple[int, int] = DEFAULT_DATA_END) -> str:
    from trading.research.robustness import trial_id_for

    return trial_id_for(family=FAMILY, params=trial_params(candidates), dataset=dataset_description(data_end))


def require_registration(registry: Path, candidates: Sequence[Mapping[str, Any]],
                         data_end: tuple[int, int] = DEFAULT_DATA_END) -> str:
    from trading.research.robustness import TrialRegistry

    if not candidates:
        raise SystemExit("no candidate passed the discovery rule; the confirmation window stays sealed")
    tid = trial_id(candidates, data_end)
    if tid not in {r.trial_id for r in TrialRegistry(registry).selection_trials(FAMILY)}:
        raise SystemExit(f"trial {tid} is not pre-registered for this code and candidate list; "
                         f"refusing to look at cohorts from {iso(SEAL_DAY)}")
    return tid


def register(registry: Path, candidates: Sequence[Mapping[str, Any]], *,
             data_end: tuple[int, int] = DEFAULT_DATA_END, now: int | None = None) -> str:
    from trading.research.robustness import TrialRecord, TrialRegistry

    if not candidates:
        raise SystemExit("nothing to register: no candidate passed the discovery rule")
    tid = trial_id(candidates, data_end)
    when = datetime.fromtimestamp(now or int(time.time()), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    names = ", ".join(f"{c['indicator']} ({c['direction']})" for c in candidates)
    record = TrialRecord(
        trial_id=tid, family=FAMILY, kind="SELECTION_CANDIDATE",
        description=(f"PRE-REGISTERED {when[:10]} after the discovery run and before any confirmation cohort "
                     f"({CONFIRMATION_YEARS[0]}-09..{CONFIRMATION_YEARS[-1]}-09) was read: candidates {names} "
                     f"chosen by the protocol's own rule (docs/BREAKOUT_COHORT.md §6.5) on the non-overlapping "
                     f"annual discovery cohorts {DISCOVERY_YEARS[0]}-09..{DISCOVERY_YEARS[-1]}-09 of the "
                     f"survivorship-free Binance USDT spot universe. Within-cohort lift, stratified bootstrap over "
                     f"coins, one confirmation run. Research only, no order authority."),
        params=trial_params(candidates), dataset=dataset_description(data_end), recorded_at=when,
        n_trades=None, sharpe_per_trade=None,
    )
    TrialRegistry(registry).append(record)
    return tid


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def run_discovery(data: Dataset, *, reps: int = RANDOM_REPS, descriptive: bool = True) -> dict[str, Any]:
    if not purge_satisfied():
        raise RuntimeError("discovery labels overlap the confirmation window; protocol constants are wrong")
    cohorts = inference_cohorts(data, DISCOVERY_YEARS)
    stats = {name: indicator_stats(cohorts, name) for name in COIN_INDICATORS}
    for name in PERP_INDICATORS:
        stats[name] = {**indicator_stats(cohorts, name), "descriptive_only": True}
    candidates = select_candidates(stats)
    report: dict[str, Any] = {
        "schema": SCHEMA, "family": FAMILY, "stage": "discovery", "code_fingerprint": code_fingerprint(),
        "data_end": iso(data.data_end_day), "cohorts": [c.name for c in cohorts],
        "base_rates": {c.name: base_rate(c) for c in cohorts},
        "indicators": stats, "candidates": candidates,
        "random_control": random_control(cohorts, reps=reps),
        "period_cards": [period_card(c) for c in cohorts],
        "sigma_wick_table": sigma_wick_table(cohorts),
        "can_authorize_trade": False,
    }
    if descriptive:
        monthly = descriptive_cohorts(data, before=SEAL_DAY)
        report["monthly"] = {"origins": [c.name for c in monthly],
                             "base_rates": {c.name: base_rate(c) for c in monthly},
                             "sigma_wick_table": sigma_wick_table(monthly),
                             "period_cards": [period_card(c) for c in monthly]}
    return report


def run_confirmation(data: Dataset, discovery: Mapping[str, Any], registry: Path) -> dict[str, Any]:
    candidates = list(discovery.get("candidates") or [])
    data_end = tuple(int(x) for x in discovery["data_end"].split("-")[:2])
    tid = require_registration(registry, candidates, data_end)  # noqa: F841  (refuses before any read)
    cohorts = inference_cohorts(data, CONFIRMATION_YEARS)
    family_alpha = ALPHA / max(1, len(candidates))
    results = {}
    for cand in candidates:
        name = cand["indicator"]
        stats = indicator_stats(cohorts, name, alpha=family_alpha)
        labelled = sum(len(_labelled(c)) for c in cohorts)
        members = sum(len(c.rows) for c in cohorts)
        stats["unknown_share"] = (members - labelled) / members if members else 1.0
        results[name] = {**stats, "discovery": discovery["indicators"][name],
                         "verdict": verdict(discovery["indicators"][name], stats, family_alpha=family_alpha)}
    return {
        "schema": SCHEMA, "family": FAMILY, "stage": "confirmation", "trial_id": tid,
        "code_fingerprint": code_fingerprint(), "cohorts": [c.name for c in cohorts],
        "base_rates": {c.name: base_rate(c) for c in cohorts}, "family_alpha": family_alpha,
        "results": results, "period_cards": [period_card(c) for c in cohorts],
        "sigma_wick_table": sigma_wick_table(cohorts), "can_authorize_trade": False,
    }


# ---------------------------------------------------------------------------
# Data build (data.binance.vision, DefiLlama, alternative.me, research/atlas)
# ---------------------------------------------------------------------------

STABLECOINS_URL = "https://stablecoins.llama.fi/stablecoincharts/all"
FNG_URL = "https://api.alternative.me/fng/?limit=0&format=json"
PERP_KLINES_PREFIX = "data/futures/um/monthly/klines/"
PERP_FUNDING_PREFIX = "data/futures/um/monthly/fundingRate/"


def _list_months(session, prefix: str) -> list[str]:
    from trading.data import binance_universe as bu
    from trading.data.binance_listings import parse_month_keys
    from trading.data.binance_perp import DataQualityError

    months: list[str] = []
    marker = None
    for _ in range(20):
        params = {"prefix": prefix}
        if marker:
            params["marker"] = marker
        response = bu._get(session, bu.S3_LIST_URL, params=params)
        if response is None:
            return []
        page, marker = parse_month_keys(response.text)
        months.extend(page)
        if marker is None:
            return sorted(set(months))
    raise DataQualityError("kline listing did not terminate")


def _download_daily(session, market: str, symbol: str, months: Sequence[str], *, decision_at: int) -> list[tuple]:
    from trading.data.binance_carry_universe import _csv, kline_url
    from trading.data.binance_vision import parse_kline_csv

    rows: list[tuple] = []
    for ym in months:
        year, month = (int(x) for x in ym.split("-"))
        text = _csv(session, kline_url(market, symbol, "1d", year, month), f"{symbol} 1d {ym}")
        if text is None:
            continue
        for c in parse_kline_csv(text, decision_at=decision_at):
            if c.open_time % DAY == 0:
                rows.append((c.open_time // DAY, c.open, c.high, c.low, c.close, c.quote_volume))
    return rows


def _download_funding(session, perp: str, months: Sequence[str], *, decision_at: int) -> list[tuple[int, float]]:
    from trading.data.binance_carry_universe import _csv, funding_url, parse_funding

    out: list[tuple[int, float]] = []
    for ym in months:
        year, month = (int(x) for x in ym.split("-"))
        text = _csv(session, funding_url(perp, year, month), f"{perp} funding {ym}")
        if text:
            out.extend((t, r) for t, r in parse_funding(text) if t <= decision_at)
    return sorted(set(out))


def _write_rows(rows: Sequence[Sequence[Any]], columns: Sequence[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)
    tmp.replace(path)


def _read_rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        return [row for row in reader if row]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_stablecoins(session) -> list[tuple[int, float]]:
    response = session.get(STABLECOINS_URL, timeout=60)
    response.raise_for_status()
    out = []
    for row in response.json():
        try:
            day = int(row["date"]) // DAY
            value = float((row.get("totalCirculatingUSD") or {}).get("peggedUSD"))
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            out.append((day, value))
    return sorted(dict(out).items())


def fetch_fng(session) -> list[tuple[int, float]]:
    response = session.get(FNG_URL, timeout=60)
    response.raise_for_status()
    out = []
    for row in response.json().get("data") or []:
        try:
            out.append((int(row["timestamp"]) // DAY, float(row["value"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(dict(out).items())


def build(out_dir: Path, *, data_end: tuple[int, int] = DEFAULT_DATA_END, symbols: Sequence[str] | None = None,
          workers: int = 16, session=None) -> dict[str, Any]:
    """Download everything the cohorts need; writes CSV per symbol and a manifest with hashes."""

    import requests
    from trading.data import binance_universe as bu
    from trading.data.binance_carry_universe import PERP_PREFIX, list_symbols, map_perps_to_spot
    from trading.data.binance_history_identity import exclusion_reason

    session = session or requests.Session()
    data_end_day = day_of(*data_end)
    decision_at = data_end_day * DAY
    spots = bu.filter_usdt_pairs(bu.list_spot_symbols(session))
    bases = {s[:-4] for s in spots.eligible}
    excluded: dict[str, str] = {s: "STABLE" for s in spots.stable}
    excluded.update({s: "LEVERAGED" for s in spots.leveraged})
    eligible = []
    for symbol in spots.eligible:
        reason = exclusion_reason(symbol[:-4], bases)
        if reason:
            excluded[symbol] = reason
        else:
            eligible.append(symbol)
    if symbols:
        wanted = {s.upper() for s in symbols} | {BTC}
        eligible = [s for s in eligible if s in wanted]
    perps = list_symbols(session, PERP_PREFIX)
    mapping = map_perps_to_spot(perps, eligible)
    spot_of_perp = {p: s for p, s in mapping.mapped.items() if s in set(eligible)}

    def one_spot(symbol: str) -> tuple[str, list[tuple], list[str]]:
        months = _list_months(session, f"{bu.SPOT_KLINES_PREFIX}{symbol}/1d/")
        return symbol, _download_daily(session, "spot", symbol, months, decision_at=decision_at), months

    def one_perp(perp: str) -> tuple[str, list[tuple], list[tuple[int, float]], list[str]]:
        months = _list_months(session, f"{PERP_KLINES_PREFIX}{perp}/1d/")
        rows = _download_daily(session, "futures/um", perp, months, decision_at=decision_at)
        fmonths = _list_months(session, f"{PERP_FUNDING_PREFIX}{perp}/")
        return perp, rows, _download_funding(session, perp, fmonths, decision_at=decision_at), months

    manifest: dict[str, Any] = {"schema": SCHEMA, "data_end": f"{data_end[0]:04d}-{data_end[1]:02d}",
                                "built_at": int(time.time()), "spot": {}, "perp": {}, "excluded": excluded,
                                "perps_without_spot": list(mapping.no_spot), "files": {}}
    columns = ("day", "open", "high", "low", "close", "quote_volume")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for symbol, rows, months in pool.map(one_spot, eligible):
            if not rows:
                manifest["spot"][symbol] = {"rows": 0, "months": months}
                continue
            path = out_dir / "spot" / f"{symbol}.csv"
            _write_rows(rows, columns, path)
            manifest["spot"][symbol] = {"rows": len(rows), "months": months, "first_day": iso(rows[0][0]),
                                        "last_day": iso(rows[-1][0])}
            manifest["files"][f"spot/{symbol}.csv"] = _sha256(path)
        for perp, rows, funding, months in pool.map(one_perp, sorted(spot_of_perp)):
            spot = spot_of_perp[perp]
            entry: dict[str, Any] = {"perp": perp, "rows": len(rows), "funding_rows": len(funding), "months": months}
            if rows:
                path = out_dir / "perp" / f"{spot}.csv"
                _write_rows(rows, columns, path)
                manifest["files"][f"perp/{spot}.csv"] = _sha256(path)
            if funding:
                path = out_dir / "funding" / f"{spot}.csv"
                _write_rows(funding, ("funding_time", "rate"), path)
                manifest["files"][f"funding/{spot}.csv"] = _sha256(path)
            manifest["perp"][spot] = entry
    stable = fetch_stablecoins(session)
    _write_rows(stable, ("day", "total_usd"), out_dir / "stablecoins.csv")
    fng = fetch_fng(session)
    _write_rows(fng, ("day", "value"), out_dir / "fng.csv")
    for name in ("stablecoins.csv", "fng.csv"):
        manifest["files"][name] = _sha256(out_dir / name)
    tmp = out_dir / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True), "utf-8")
    tmp.replace(out_dir / "manifest.json")
    return manifest


def load(data_dir: Path, *, atlas_dir: Path = REPO_ROOT / "research" / "atlas") -> Dataset:
    manifest = json.loads((data_dir / "manifest.json").read_text("utf-8"))
    for name, digest in manifest.get("files", {}).items():
        if _sha256(data_dir / name) != digest:
            raise ValueError(f"{name}: content does not match the manifest")
    year, month = (int(x) for x in manifest["data_end"].split("-"))
    data = Dataset(spot={}, data_end_day=day_of(year, month))

    def read_series(path: Path, symbol: str) -> Series | None:
        rows = [(int(r[0]), *(float(x) for x in r[1:6])) for r in _read_rows(path)]
        return series_from_rows(symbol, rows) if rows else None

    for symbol in manifest["spot"]:
        path = data_dir / "spot" / f"{symbol}.csv"
        if path.exists():
            series = read_series(path, symbol)
            if series is not None:
                data.spot[symbol] = series
    for spot in manifest.get("perp", {}):
        path = data_dir / "perp" / f"{spot}.csv"
        if path.exists():
            series = read_series(path, spot)
            if series is not None:
                data.perp[spot] = series
        fpath = data_dir / "funding" / f"{spot}.csv"
        if fpath.exists():
            data.funding[spot] = [(int(r[0]), float(r[1])) for r in _read_rows(fpath)]
    for name, target in (("stablecoins.csv", data.stablecoins), ("fng.csv", data.fng)):
        path = data_dir / name
        if path.exists():
            target.update({int(r[0]): float(r[1]) for r in _read_rows(path)})
    monthly, daily = atlas_dir / "macro_monthly.csv", atlas_dir / "macro_daily.csv"
    if monthly.exists():
        with monthly.open(newline="", encoding="utf-8") as handle:
            data.macro_monthly = {row["month"]: row for row in csv.DictReader(handle)}
    if daily.exists():
        with daily.open(newline="", encoding="utf-8") as handle:
            data.macro_daily = {row["date"]: row for row in csv.DictReader(handle)}
    return data


# ---------------------------------------------------------------------------
# Report text and CLI
# ---------------------------------------------------------------------------


def _fmt(value: Any, spec: str = "+.3f") -> str:
    return "n/a" if value is None else format(value, spec)


def print_summary(report: Mapping[str, Any]) -> None:
    stage = report.get("stage")
    print(f"breakout cohort — {stage} — cohorts {', '.join(report['cohorts'])} — fingerprint "
          f"{report['code_fingerprint']}")
    for name, base in report["base_rates"].items():
        print(f"  {name}: members {base['members']}, labelled {base['labelled']}, 2x in 12m "
              f"{_fmt(base['hit_rate_12m'], '.2f')}, 24m {_fmt(base['hit_rate_24m'], '.2f')}, "
              f"delisted {base['delisted']}")
    if stage == "discovery":
        print("indicator: lift (known cohorts, signs agree) ci")
        for name, stats in report["indicators"].items():
            flag = " [descriptive]" if stats.get("descriptive_only") else ""
            print(f"  {name:<13} {_fmt(stats['lift'])} ({stats['known']}, {stats['signs_agree']}) "
                  f"{stats['ci']}{flag}")
        print("candidates:", [(c["indicator"], c["direction"]) for c in report["candidates"]] or "none")
        rc = report["random_control"]
        print(f"random control: {rc['passes']}/{rc['reps']} random buckets pass the candidate rule")
    else:
        for name, result in report["results"].items():
            print(f"  {name:<13} discovery {_fmt(result['discovery']['lift'])} -> confirmation "
                  f"{_fmt(result['lift'])} {result['ci']} ({result['known']} cohorts) → {result['verdict']}")
    print("can_authorize_trade: false")


def _write_json(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str), "utf-8")
    tmp.replace(path)


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Breakout cohort study (docs/BREAKOUT_COHORT.md). Research only.")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--data-end", default=f"{DEFAULT_DATA_END[0]:04d}-{DEFAULT_DATA_END[1]:02d}",
                   help="first month NOT covered (default: the protocol's data end)")
    b.add_argument("--symbols", nargs="*", help="smoke test: only these spot symbols (BTCUSDT is always added)")
    b.add_argument("--workers", type=int, default=16)
    d = sub.add_parser("discover")
    d.add_argument("--data-dir", type=Path, required=True)
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--random-reps", type=int, default=RANDOM_REPS)
    r = sub.add_parser("register")
    r.add_argument("--report", type=Path, required=True)
    r.add_argument("--registry", type=Path, default=REPO_ROOT / "research" / "trials" / "registry.jsonl")
    c = sub.add_parser("confirm")
    c.add_argument("--data-dir", type=Path, required=True)
    c.add_argument("--report", type=Path, required=True, help="the discovery report (its candidates)")
    c.add_argument("--registry", type=Path, default=REPO_ROOT / "research" / "trials" / "registry.jsonl")
    c.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "build":
        year, month = (int(x) for x in args.data_end.split("-"))
        manifest = build(args.out, data_end=(year, month), symbols=args.symbols, workers=args.workers)
        print(f"spot symbols {len(manifest['spot'])}, perps {len(manifest['perp'])}, "
              f"excluded {len(manifest['excluded'])}, data end {manifest['data_end']}")
        return 0
    if args.command == "discover":
        report = run_discovery(load(args.data_dir), reps=args.random_reps)
        _write_json(report, args.out)
        print_summary(report)
        return 0
    if args.command == "register":
        report = json.loads(args.report.read_text("utf-8"))
        if report.get("code_fingerprint") != code_fingerprint():
            raise SystemExit("the discovery report was produced by other code; re-run discovery first")
        data_end = tuple(int(x) for x in report["data_end"].split("-")[:2])
        tid = register(args.registry, report.get("candidates") or [], data_end=data_end)
        print(f"registered {tid}")
        return 0
    if args.command == "confirm":
        discovery = json.loads(args.report.read_text("utf-8"))
        if discovery.get("code_fingerprint") != code_fingerprint():
            raise SystemExit("the discovery report was produced by other code; refusing to confirm")
        report = run_confirmation(load(args.data_dir), discovery, args.registry)
        _write_json(report, args.out)
        print_summary(report)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
