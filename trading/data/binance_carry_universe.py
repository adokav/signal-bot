"""Survivorship-free perp + spot + funding data for the funding-carry replay.

The carry trade (long spot, short the USDⓈ-M perpetual) earns the funding
that leveraged longs pay. Testing it honestly needs every perpetual that
*was* liquid, including delisted ones, its spot counterpart and its full
funding history. Everything comes from data.binance.vision:

1. **List** every USDⓈ-M perpetual and every spot pair ever published (S3
   bucket listing, delisted pairs included). Keep USDT perps that are not
   stablecoins and map each to its spot pair (``1000PEPEUSDT`` -> ``PEPEUSDT``;
   returns are ratios, so the 1000x contract multiplier does not matter).
2. **Rank daily** by perp quote volume (monthly 1d klines). A perp becomes a
   candidate for a month when it ranked in the top ``rank_limit`` on some day.
   This only decides what to download; the replay re-ranks point in time.
3. **Download** 8h klines for perp and spot plus monthly funding for the
   candidate months and one month either side.

Files that fingerprint other pre-registered trials (``binance_universe.py``)
are reused, never modified. Failure policy as there: 404 is "not
published", anything else is retried and then aborts (AGENTS.md §2).

Research only; no order authority (AGENTS.md §4).
"""

from __future__ import annotations

import io
import json
import logging
import math
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import requests

from acce_unified.cex import is_stable_or_synthetic
from trading.data.binance_perp import DataQualityError
from trading.data.binance_universe import (
    S3_LIST_URL,
    DownloadError,
    _get,
    candidate_months,
    coverage_end,
)
from trading.data.binance_vision import (
    BASE_URL,
    _latest_completed_month,
    _session,
    epoch_seconds,
    months_ending_at,
    parse_kline_csv,
)


log = logging.getLogger(__name__)

PERP_PREFIX = "data/futures/um/monthly/klines/"
SPOT_PREFIX = "data/spot/monthly/klines/"
QUOTE = "USDT"
MIN_PERPS = 150  # far fewer means the listing is broken, not the market
DEFAULT_RANK_LIMIT = 40
DEFAULT_MIN_QUOTE_VOLUME = 10_000_000.0
# Contract-size prefixes Binance uses for low-priced coins (1000PEPE, 1MBABYDOGE).
MULTIPLIER_PREFIXES = (("1000000", 1_000_000), ("1000", 1_000), ("1M", 1_000_000))


# ---------------------------------------------------------------------------
# Listing and symbol mapping
# ---------------------------------------------------------------------------


def parse_listing(xml_text: str, prefix: str) -> tuple[list[str], str | None]:
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise DataQualityError("unreadable bucket listing") from exc
    ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
    names = []
    for node in root.iter(f"{ns}Prefix"):
        value = (node.text or "").strip()
        if value.startswith(prefix) and value.endswith("/") and value != prefix:
            names.append(value[len(prefix):-1])
    truncated = (root.findtext(f"{ns}IsTruncated") or "").strip().lower() == "true"
    marker = (root.findtext(f"{ns}NextMarker") or "").strip() or None
    if truncated and marker is None:
        raise DataQualityError("truncated listing without a continuation marker")
    return names, (marker if truncated else None)


def list_symbols(session: requests.Session, prefix: str, *, max_pages: int = 100) -> list[str]:
    out: list[str] = []
    marker = None
    for _ in range(max_pages):
        params = {"delimiter": "/", "prefix": prefix}
        if marker:
            params["marker"] = marker
        response = _get(session, S3_LIST_URL, params=params)
        if response is None:
            raise DataQualityError("bucket listing not found")
        page, marker = parse_listing(response.text, prefix)
        out.extend(page)
        if marker is None:
            return sorted(set(out))
    raise DataQualityError("bucket listing did not terminate")


@dataclass(frozen=True)
class PerpMapping:
    mapped: dict[str, str]          # perp -> spot
    no_spot: tuple[str, ...]
    stable: tuple[str, ...]


def map_perps_to_spot(perps: Iterable[str], spots: Iterable[str], *, quote: str = QUOTE) -> PerpMapping:
    spot_set = {s.upper() for s in spots}
    mapped, no_spot, stable = {}, [], []
    for perp in sorted({p.upper() for p in perps}):
        if not perp.endswith(quote) or len(perp) <= len(quote):
            continue
        base = perp[: -len(quote)]
        candidates = [base]
        for prefix, _ in MULTIPLIER_PREFIXES:
            if base.startswith(prefix) and len(base) > len(prefix):
                candidates.append(base[len(prefix):])
        if any(is_stable_or_synthetic(c) for c in candidates):
            stable.append(perp)
            continue
        spot = next((c + quote for c in candidates if c + quote in spot_set), None)
        if spot is None:
            no_spot.append(perp)
        else:
            mapped[perp] = spot
    return PerpMapping(mapped, tuple(no_spot), tuple(stable))


# ---------------------------------------------------------------------------
# Downloads and parsing
# ---------------------------------------------------------------------------


def kline_url(market: str, symbol: str, interval: str, year: int, month: int) -> str:
    return (f"{BASE_URL}/data/{market}/monthly/klines/{symbol}/{interval}/"
            f"{symbol}-{interval}-{year:04d}-{month:02d}.zip")


def funding_url(symbol: str, year: int, month: int) -> str:
    return (f"{BASE_URL}/data/futures/um/monthly/fundingRate/{symbol}/"
            f"{symbol}-fundingRate-{year:04d}-{month:02d}.zip")


def _csv(session: requests.Session, url: str, what: str) -> str | None:
    response = _get(session, url)
    if response is None:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise DataQualityError("no CSV in archive")
            return archive.read(names[0]).decode("utf-8", errors="replace")
    except Exception as exc:  # a corrupt archive is a hole, not an empty month
        raise DownloadError(f"{what}: {type(exc).__name__}") from exc


def parse_funding(csv_text: str) -> list[tuple[int, float]]:
    """(funding_time seconds, rate) rows; header or headerless, ms or µs timestamps.

    Rows with an unknown timestamp width, a non-finite rate or a rate outside
    (-1, 1) are dropped; the caller counts how many rows survived.
    """

    rows = [[c.strip() for c in line.split(",")] for line in csv_text.splitlines() if line.strip()]
    if not rows:
        return []
    time_idx, rate_idx = 0, None
    first = rows[0][0].lower()
    if not first.lstrip("-").isdigit():
        header = [h.lower() for h in rows[0]]
        rows = rows[1:]
        time_idx = next((k for k, h in enumerate(header) if "time" in h), 0)
        rate_idx = next((k for k, h in enumerate(header) if "rate" in h), None)
    out = []
    for row in rows:
        try:
            idx = rate_idx if rate_idx is not None else next(
                k for k in range(1, len(row)) if "." in row[k] or "e" in row[k].lower()
            )
            rate = float(row[idx])
            t = epoch_seconds(row[time_idx])
        except (StopIteration, ValueError, IndexError, DataQualityError):
            continue
        if math.isfinite(rate) and -1.0 < rate < 1.0 and t > 0:
            out.append((t, rate))
    return sorted(set(out))


def daily_perp_volumes(session, perps: Sequence[str], months, *, decision_at: int, workers: int = 32):
    tasks = [(p, y, m) for p in perps for y, m in months]

    def run(task):
        perp, year, month = task
        text = _csv(session, kline_url("futures/um", perp, "1d", year, month), f"{perp} 1d {year}-{month:02d}")
        if text is None:
            return perp, []
        return perp, [(c.open_time - c.open_time % 86_400, c.quote_volume)
                      for c in parse_kline_csv(text, decision_at=decision_at)]

    out: dict[str, dict[int, float]] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for perp, days in pool.map(run, tasks):
            if days:
                out.setdefault(perp, {}).update(days)
    return out


def _frame_records(candles) -> list[dict]:
    return [{"open_time": c.open_time, "open": c.open, "high": c.high, "low": c.low,
             "close": c.close, "quote_volume": c.quote_volume} for c in candles]


def _write(records: list[dict], path: Path) -> None:
    import pandas as pd

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pd.DataFrame.from_records(records).to_parquet(tmp, index=False)
    tmp.replace(path)


def download_pair(session, perp: str, spot: str, months, out_dir: Path, *, decision_at: int, workers: int = 16) -> dict:
    def klines(market, symbol):
        def one(ym):
            text = _csv(session, kline_url(market, symbol, "8h", *ym), f"{symbol} 8h {ym[0]}-{ym[1]:02d}")
            return parse_kline_csv(text, decision_at=decision_at) if text else []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            candles = [c for part in pool.map(one, months) for c in part]
        seen, unique = set(), []
        for c in sorted(candles, key=lambda c: c.open_time):
            if c.open_time not in seen:
                seen.add(c.open_time)
                unique.append(c)
        return unique

    def funding():
        def one(ym):
            text = _csv(session, funding_url(perp, *ym), f"{perp} funding {ym[0]}-{ym[1]:02d}")
            return [r for r in parse_funding(text) if r[0] <= decision_at] if text else []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return sorted({r for part in pool.map(one, months) for r in part})

    perp_rows, spot_rows, fund_rows = klines("futures/um", perp), klines("spot", spot), funding()
    if not perp_rows or not spot_rows:
        return {}
    _write(_frame_records(perp_rows), out_dir / "perp" / f"{perp}.parquet")
    _write(_frame_records(spot_rows), out_dir / "spot" / f"{perp}.parquet")
    if fund_rows:  # no file means no funding history; the replay counts that, never invents rates
        _write([{"funding_time": t, "rate": r} for t, r in fund_rows], out_dir / "funding" / f"{perp}.parquet")
    return {
        "spot": spot,
        "months": [f"{y:04d}-{m:02d}" for y, m in months],
        "perp_bars": len(perp_rows), "spot_bars": len(spot_rows), "funding_rows": len(fund_rows),
        "perp_last_close": perp_rows[-1].close_time, "spot_last_close": spot_rows[-1].close_time,
    }


def build_carry_universe(
    out_dir: Path,
    *,
    lookback_months: int,
    decision_at: int | None = None,
    rank_limit: int = DEFAULT_RANK_LIMIT,
    min_quote_volume: float = DEFAULT_MIN_QUOTE_VOLUME,
    session: requests.Session | None = None,
) -> dict:
    decision_at = int(decision_at or time.time())
    if session is None:
        session = _session(timeout=60)
        session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=64))
    end_year, end_month = _latest_completed_month(decision_at)
    window = sorted(months_ending_at(end_year=end_year, end_month=end_month, lookback_months=lookback_months))

    perps = list_symbols(session, PERP_PREFIX)
    spots = list_symbols(session, SPOT_PREFIX)
    mapping = map_perps_to_spot(perps, spots)
    if len(mapping.mapped) + len(mapping.no_spot) + len(mapping.stable) < MIN_PERPS:
        raise DataQualityError(f"only {len(mapping.mapped)} USDT perps listed; listing looks incomplete")
    log.info("%d perps listed, %d mapped to spot", len(perps), len(mapping.mapped))

    daily = daily_perp_volumes(session, sorted(mapping.mapped), window, decision_at=decision_at)
    plan = candidate_months(daily, window=window, rank_limit=rank_limit, min_quote_volume=min_quote_volume)
    files = {}
    for perp, months in plan.items():
        info = download_pair(session, perp, mapping.mapped[perp], months, out_dir, decision_at=decision_at)
        if info:
            files[perp] = info
            log.info("%s/%s: %d perp bars, %d funding rows", perp, info["spot"], info["perp_bars"], info["funding_rows"])
    data_end = max((min(i["perp_last_close"], i["spot_last_close"]) for i in files.values()), default=0)
    stopped = sorted(
        p for p, i in files.items()
        if min(i["perp_last_close"], i["spot_last_close"]) < min(coverage_end(plan[p][-1]), data_end) - 2 * 86_400
    )
    manifest = {
        "schema": "carry-universe/v1",
        "generated_at": decision_at,
        "window": [f"{window[0][0]:04d}-{window[0][1]:02d}", f"{window[-1][0]:04d}-{window[-1][1]:02d}"],
        "rank_limit": rank_limit,
        "min_quote_volume": min_quote_volume,
        "perps_listed": len(perps),
        "mapping": {"mapped": len(mapping.mapped), "no_spot": len(mapping.no_spot), "stable": len(mapping.stable)},
        "candidates": len(plan),
        "downloaded": len(files),
        "data_end": data_end,
        "stopped_before_window_end": stopped,
        "symbols": files,
        "can_authorize_trade": False,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=1), "utf-8")
    tmp.replace(out_dir / "manifest.json")
    return manifest


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Download the survivorship-free perp/spot/funding universe.")
    parser.add_argument("--out", type=Path, default=Path("research/data/carry_universe"))
    parser.add_argument("--lookback-months", type=int, default=72)
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    manifest = build_carry_universe(args.out, lookback_months=args.lookback_months)
    print(json.dumps({k: v for k, v in manifest.items() if k != "symbols"}, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
