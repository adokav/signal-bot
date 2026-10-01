"""Perpetual order-flow and open-interest data for the majors study. Research only.

docs/MAJORS_STUDY.md tests three perpetual-market signals on a point-in-time
majors universe. Funding comes from ``universe_funding``; this module adds the
two inputs that study needs and nothing else:

- **taker flow:** 1h USDⓈ-M perpetual klines *with* the taker-buy quote
  volume (``binance_vision.parse_kline_csv`` drops that column);
- **open interest:** the 5-minute ``metrics`` dumps (``sum_open_interest``).

Downloads cover only the months/days a pair is a universe member (plus the
history its features look back over), and keep only rows visible at or
before ``decision_at``: candles closed by then, OI snapshots created by then.

Failure policy as in ``binance_universe``: 404 means "not published" and is
recorded per file; any other failure is retried and then aborts the build. A
row that fails validation is dropped and counted, never repaired. Missing
data stays missing (AGENTS.md §2). Writes are atomic.

Files that fingerprint pre-registered trials are imported, never modified.
No order authority (AGENTS.md §4).
"""

from __future__ import annotations

import csv
import gzip
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import requests

from trading.data import binance_carry_universe as carry
from trading.data.binance_vision import _is_kline_header, _split_row, epoch_seconds
from trading.data.universe_funding import _pooled_session, multiplier_of


# Static meme list, fixed before any result (docs/MAJORS_STUDY.md).
MEMES = frozenset({
    "DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF", "BOME", "MEME", "PEOPLE", "TURBO", "NEIRO",
    "1000SATS", "1MBABYDOGE", "DOGS", "PNUT", "ACT", "TRUMP", "PENGU",
})

TAKER_COLUMNS = ("spot_symbol", "perp_symbol", "multiplier", "open_time", "close", "quote_volume", "taker_buy_quote")
OI_COLUMNS = ("spot_symbol", "perp_symbol", "create_time", "sum_open_interest")
HOUR = 3600


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_taker_klines(csv_text: str, *, decision_at: int, interval: int = HOUR) -> tuple[list[tuple], int]:
    """(open_time, close, quote_volume, taker_buy_quote) of candles closed by ``decision_at``; and rejects.

    A row is rejected when a field is missing or non-finite, the time range is
    not one ``interval``, a price is not positive, OHLC is impossible, a volume
    is negative, or the taker-buy volume exceeds the total.
    """

    rows = [_split_row(line) for line in csv_text.splitlines() if line.strip()]
    if rows and _is_kline_header(rows[0]):
        rows = rows[1:]
    out, rejected = [], 0
    for row in rows:
        try:
            open_time, close_time = epoch_seconds(row[0]), epoch_seconds(row[6])
            o, h, low, c = (float(row[k]) for k in (1, 2, 3, 4))
            quote, taker = float(row[7]), float(row[10])
        except (ValueError, IndexError, carry.DataQualityError):
            rejected += 1
            continue
        values = (o, h, low, c, quote, taker)
        ok = (
            all(math.isfinite(v) for v in values)
            and close_time + 1 - open_time == interval
            and min(o, h, low, c) > 0 and low <= min(o, c) and h >= max(o, c)
            and quote >= 0 and 0 <= taker <= quote * (1 + 1e-9)
        )
        if not ok:
            rejected += 1
            continue
        if close_time <= decision_at:
            out.append((open_time, c, quote, taker))
    return out, rejected


def _create_time(raw: str) -> int:
    raw = raw.strip()
    if raw.lstrip("-").isdigit():
        return epoch_seconds(raw)
    return int(datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def parse_metrics(csv_text: str, *, decision_at: int) -> tuple[list[tuple[int, float]], int]:
    """(create_time, sum_open_interest) snapshots created by ``decision_at``; and rejects."""

    lines = [line for line in csv_text.splitlines() if line.strip()]
    if not lines:
        return [], 0
    header = [h.strip().strip('"').lower() for h in lines[0].split(",")]
    try:
        t_idx, oi_idx = header.index("create_time"), header.index("sum_open_interest")
    except ValueError as exc:
        raise carry.DataQualityError("metrics file without create_time/sum_open_interest") from exc
    out, rejected = [], 0
    for line in lines[1:]:
        cells = [c.strip().strip('"') for c in line.split(",")]
        try:
            t, oi = _create_time(cells[t_idx]), float(cells[oi_idx])
        except (ValueError, IndexError, carry.DataQualityError):
            rejected += 1
            continue
        if not math.isfinite(oi) or oi <= 0:
            rejected += 1
            continue
        if t <= decision_at:
            out.append((t, oi))
    return out, rejected


def metrics_url(perp: str, day: date) -> str:
    return f"{carry.BASE_URL}/data/futures/um/daily/metrics/{perp}/{perp}-metrics-{day.isoformat()}.zip"


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


def fetch_taker(session: requests.Session, perp: str, months: Iterable[str], *,
                decision_at: int) -> tuple[list[tuple], dict]:
    rows: dict[int, tuple] = {}
    report = {"missing_months": [], "rejected_rows": 0}
    for month in sorted(set(months)):
        year, mon = (int(x) for x in month.split("-"))
        text = carry._csv(session, carry.kline_url("futures/um", perp, "1h", year, mon), f"{perp} 1h {month}")
        if text is None:
            report["missing_months"].append(month)
            continue
        parsed, rejected = parse_taker_klines(text, decision_at=decision_at)
        report["rejected_rows"] += rejected
        for row in parsed:
            if row[0] in rows and rows[row[0]] != row:
                raise carry.DataQualityError(f"{perp}: conflicting 1h candles")
            rows[row[0]] = row
    return [rows[t] for t in sorted(rows)], report


def fetch_oi(session: requests.Session, perp: str, days: Iterable[date], *,
             decision_at: int) -> tuple[list[tuple[int, float]], dict]:
    rows: dict[int, float] = {}
    report = {"missing_days": 0, "rejected_rows": 0}
    for day in sorted(set(days)):
        text = carry._csv(session, metrics_url(perp, day), f"{perp} metrics {day.isoformat()}")
        if text is None:
            report["missing_days"] += 1
            continue
        parsed, rejected = parse_metrics(text, decision_at=decision_at)
        report["rejected_rows"] += rejected
        for t, oi in parsed:
            if t in rows and rows[t] != oi:
                raise carry.DataQualityError(f"{perp}: conflicting OI snapshots")
            rows[t] = oi
    return sorted(rows.items()), report


def _month(t: int) -> str:
    d = datetime.fromtimestamp(t, tz=timezone.utc)
    return f"{d.year:04d}-{d.month:02d}"


def _previous_month(month: str) -> str:
    year, mon = (int(x) for x in month.split("-"))
    return f"{year - 1:04d}-12" if mon == 1 else f"{year:04d}-{mon - 1:02d}"


def plan_downloads(memberships: Mapping[int, Sequence[str]], *, oi_from: int,
                   month_end: Mapping[int, int]) -> tuple[dict[str, set[str]], dict[str, set[date]]]:
    """Months of 1h klines and days of OI files each spot pair needs.

    ``memberships``: month start -> member spot pairs; ``month_end``: month start ->
    the next month start. Klines: the member month and the one before (30-day
    flow baseline). OI: for each decision day d (00:00 UTC) on or after
    ``oi_from``, the files of d−2 and d−1 (snapshots at d−5min and d−24h−5min).
    """

    months: dict[str, set[str]] = {}
    days: dict[str, set[date]] = {}
    for start, members in memberships.items():
        month = _month(start)
        for spot in members:
            months.setdefault(spot, set()).update({month, _previous_month(month)})
        t = max(start, oi_from)
        while t < month_end[start]:
            d = datetime.fromtimestamp(t, tz=timezone.utc).date()
            for spot in members:
                days.setdefault(spot, set()).update({d - timedelta(days=1), d - timedelta(days=2)})
            t += 86_400
    return months, days


def build_perp_data(
    memberships: Mapping[int, Sequence[str]],
    month_end: Mapping[int, int],
    perp_of: Mapping[str, str],
    out_dir: Path,
    *,
    decision_at: int,
    oi_from: int,
    session: requests.Session | None = None,
    workers: int = 16,
) -> dict:
    """Download taker klines and OI for every member; write CSVs and a manifest atomically."""

    session = session or _pooled_session()
    months, days = plan_downloads(memberships, oi_from=oi_from, month_end=month_end)
    spots = sorted(set(months) | set(days))
    unmapped = [s for s in spots if s not in perp_of]
    if unmapped:
        raise carry.DataQualityError(f"members without a perpetual: {unmapped}")

    def one(spot: str):
        perp = perp_of[spot]
        taker, taker_report = fetch_taker(session, perp, months.get(spot, ()), decision_at=decision_at)
        oi, oi_report = fetch_oi(session, perp, days.get(spot, ()), decision_at=decision_at)
        return spot, perp, taker, oi, taker_report, oi_report

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_taker, tmp_oi = out_dir / "perp_taker_1h.csv.gz.tmp", out_dir / "oi_5m.csv.gz.tmp"
    report: dict[str, dict] = {}
    with gzip.open(tmp_taker, "wt", newline="", encoding="utf-8") as th, \
            gzip.open(tmp_oi, "wt", newline="", encoding="utf-8") as oh, \
            ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        tw, ow = csv.writer(th), csv.writer(oh)
        tw.writerow(TAKER_COLUMNS)
        ow.writerow(OI_COLUMNS)
        for spot, perp, taker, oi, taker_report, oi_report in pool.map(one, spots):
            factor = multiplier_of(perp, spot)
            tw.writerows((spot, perp, factor, t, repr(c), repr(q), repr(b)) for t, c, q, b in taker)
            ow.writerows((spot, perp, t, repr(v)) for t, v in oi)
            report[spot] = {
                "perp": perp, "taker_rows": len(taker), "oi_rows": len(oi),
                "taker_months_requested": len(months.get(spot, ())), "oi_days_requested": len(days.get(spot, ())),
                **{f"taker_{k}": v for k, v in taker_report.items()}, **{f"oi_{k}": v for k, v in oi_report.items()},
            }
    tmp_taker.replace(out_dir / "perp_taker_1h.csv.gz")
    tmp_oi.replace(out_dir / "oi_5m.csv.gz")
    manifest = {
        "schema": "majors-perp-data/v1",
        "decision_at": decision_at,
        "oi_from": oi_from,
        "pairs": report,
        "can_authorize_trade": False,
    }
    tmp = out_dir / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True), "utf-8")
    tmp.replace(out_dir / "manifest.json")
    return manifest


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_taker(data_dir: Path):
    """spot -> (open_time, quote_volume, taker_buy_quote) numpy arrays, chronological."""

    import numpy as np

    raw: dict[str, list[tuple[int, float, float]]] = {}
    with gzip.open(data_dir / "perp_taker_1h.csv.gz", "rt", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        if tuple(next(reader)) != TAKER_COLUMNS:
            raise carry.DataQualityError("unexpected taker columns")
        for spot, _, _, t, _, quote, taker in reader:
            raw.setdefault(spot, []).append((int(t), float(quote), float(taker)))
    out = {}
    for spot, rows in raw.items():
        rows.sort()
        arr = np.asarray(rows, dtype=float)
        out[spot] = (arr[:, 0].astype(np.int64), arr[:, 1], arr[:, 2])
    return out


def load_oi(data_dir: Path):
    """spot -> (create_time, sum_open_interest) numpy arrays, chronological."""

    import numpy as np

    raw: dict[str, list[tuple[int, float]]] = {}
    with gzip.open(data_dir / "oi_5m.csv.gz", "rt", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        if tuple(next(reader)) != OI_COLUMNS:
            raise carry.DataQualityError("unexpected OI columns")
        for spot, _, t, oi in reader:
            raw.setdefault(spot, []).append((int(t), float(oi)))
    out = {}
    for spot, rows in raw.items():
        rows.sort()
        arr = np.asarray(rows, dtype=float)
        out[spot] = (arr[:, 0].astype(np.int64), arr[:, 1])
    return out
