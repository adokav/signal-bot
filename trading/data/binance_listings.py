"""Survivorship-free dataset of new Binance spot listings (USDT pairs) for the new-listing study.

Pipeline (everything from data.binance.vision, which keeps delisted pairs):

1. **List** every spot pair and the months each one has monthly 1d klines for.
2. **Select** genuinely new assets: a USDT pair whose first month is in the
   window, whose base had no pair in any other quote before that month, and
   that is not a stablecoin / leveraged / pegged token or a known ticker
   migration (``binance_history_identity``). Every excluded pair is kept in
   the manifest with its reason.
3. **Download** 1h klines for each listing from its first month through the
   fourth month after it (enough for an entry 7 days in plus a 90-day hold),
   and BTCUSDT 1h for the whole window as the benchmark.

``data_end`` is the last second of the last month BTCUSDT has a published
file for; nothing later is read. Download failures raise (fail closed); a
404 only means the month is not published.

Research only; no order authority (AGENTS.md §4).
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import requests

from trading.data import binance_history_identity as identity
from trading.data import binance_universe as bu
from trading.data.binance_perp import Candle, DataQualityError, Timeframe
from trading.data.binance_vision import _latest_completed_month, _session

log = logging.getLogger(__name__)

QUOTE = "USDT"
OTHER_QUOTES = (
    "BTC", "ETH", "BNB", "BUSD", "USDC", "TUSD", "FDUSD", "TRY", "EUR", "BRL", "AUD", "GBP", "RUB", "UAH",
    "BIDR", "IDRT", "NGN", "PLN", "RON", "ZAR", "ARS", "JPY", "MXN", "COP", "CZK", "DAI", "PAX", "USDS",
    "BKRW", "VAI", "XRP", "TRX", "DOGE", "DOT", "AEUR", "EURI", "USDP", "BVND", "GYEN", "UST", "SOL",
    "USD1", "USDE",
)
WINDOW_START = "2020-10"
MONTHS_AFTER_FIRST = 4        # entry at +7d plus a 90-day hold ends inside first month + 4
BENCHMARK = "BTCUSDT"
_KEY = re.compile(r"<Key>([^<]+\.zip)</Key>")


def _month(ym: str) -> tuple[int, int]:
    year, month = (int(x) for x in ym.split("-"))
    return year, month


def _shift(ym: str, months: int) -> str:
    year, month = _month(ym)
    index = year * 12 + month - 1 + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def month_range(first: str, last: str) -> list[str]:
    out, current = [], first
    while current <= last:
        out.append(current)
        current = _shift(current, 1)
    return out


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def parse_month_keys(xml_text: str) -> tuple[list[str], str | None]:
    """Months ('YYYY-MM') of the monthly zips on one listing page, and the marker for the next page."""

    keys = _KEY.findall(xml_text)
    months = []
    for key in keys:
        # one archived key has an unpadded month (ADABKRW-1d-2020-8.zip); anything else is an error
        match = re.search(r"-(\d{4})-(\d{1,2})\.zip$", key)
        if match is None or not 1 <= int(match.group(2)) <= 12:
            raise DataQualityError("unexpected key in kline listing")
        months.append(f"{match.group(1)}-{int(match.group(2)):02d}")
    truncated = "<IsTruncated>true</IsTruncated>" in xml_text
    return months, (keys[-1] if truncated and keys else None)


def list_months(session: requests.Session, pair: str, *, max_pages: int = 20) -> list[str]:
    months: list[str] = []
    marker = None
    for _ in range(max_pages):
        params = {"prefix": f"{bu.SPOT_KLINES_PREFIX}{pair}/1d/"}
        if marker:
            params["marker"] = marker
        response = bu._get(session, bu.S3_LIST_URL, params=params)
        if response is None:
            raise DataQualityError("kline listing not found")
        page, marker = parse_month_keys(response.text)
        months.extend(page)
        if marker is None:
            return sorted(set(months))
    raise DataQualityError("kline listing did not terminate")


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


@dataclass
class Selection:
    listings: dict[str, str] = field(default_factory=dict)      # pair -> first month
    excluded: dict[str, str] = field(default_factory=dict)      # pair -> reason


def select_listings(
    symbols: Iterable[str],
    months_by_pair: Mapping[str, Sequence[str]],
    *,
    window_start: str = WINDOW_START,
    window_end: str,
) -> Selection:
    """New-asset USDT listings with first month in [window_start, window_end]."""

    symbols = {s.upper() for s in symbols}
    usdt = sorted(s for s in symbols if s.endswith(QUOTE) and len(s) > len(QUOTE))
    bases = {s[: -len(QUOTE)] for s in usdt}
    out = Selection()
    for pair in usdt:
        months = months_by_pair.get(pair) or []
        if not months or not (window_start <= months[0] <= window_end):
            continue
        base = pair[: -len(QUOTE)]
        first = months[0]
        reason = identity.exclusion_reason(base, bases)
        if reason is None and identity.migration_source(base):
            reason = f"MIGRATION_FROM_{identity.migration_source(base)}"
        if reason is None:
            earlier = [base + q for q in OTHER_QUOTES
                       if (base + q) in symbols and (months_by_pair.get(base + q) or ["9999-99"])[0] < first]
            if earlier:
                reason = "LISTED_EARLIER_IN_" + ",".join(sorted(earlier))
        if reason is None:
            out.listings[pair] = first
        else:
            out.excluded[pair] = reason
    return out


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


def _write(candles: Sequence[Candle], path: Path) -> None:
    import pandas as pd

    frame = pd.DataFrame.from_records([
        {"open_time": c.open_time, "open": c.open, "high": c.high, "low": c.low, "close": c.close,
         "quote_volume": c.quote_volume}
        for c in candles
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    frame.to_parquet(tmp, index=False)
    tmp.replace(path)


def download_1h(session: requests.Session, pair: str, months: Sequence[str], path: Path, *, decision_at: int,
                pool: ThreadPoolExecutor) -> dict | None:
    parts = pool.map(lambda ym: bu.fetch_month(session, pair, Timeframe.H1, *_month(ym), decision_at=decision_at),
                     months)
    seen: set[int] = set()
    candles = []
    for part in parts:
        for candle in part:
            if candle.open_time not in seen:
                seen.add(candle.open_time)
                candles.append(candle)
    if not candles:
        return None
    candles.sort(key=lambda c: c.open_time)
    _write(candles, path)
    return {"bars": len(candles), "first_open": candles[0].open_time, "last_close": candles[-1].close_time,
            "months": list(months)}


def build_listings(
    out_dir: Path,
    *,
    decision_at: int | None = None,
    window_start: str = WINDOW_START,
    session: requests.Session | None = None,
    workers: int = 32,
) -> dict:
    decision_at = int(decision_at or time.time())
    if session is None:
        session = _session(timeout=60)
        session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=64))
    symbols = bu.list_spot_symbols(session)
    usdt = [s for s in symbols if s.endswith(QUOTE) and len(s) > len(QUOTE)]
    if len(usdt) < bu.MIN_USDT_PAIRS:
        raise DataQualityError(f"only {len(usdt)} USDT pairs listed; the listing looks broken")
    symbol_set = set(symbols)
    wanted = set(usdt) | ({s[: -len(QUOTE)] + q for s in usdt for q in OTHER_QUOTES} & symbol_set)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        months_by_pair = dict(zip(sorted(wanted), pool.map(lambda p: list_months(session, p), sorted(wanted))))

    benchmark_months = [m for m in months_by_pair.get(BENCHMARK, []) if m >= window_start]
    if not benchmark_months:
        raise DataQualityError("no benchmark months")
    completed = "{:04d}-{:02d}".format(*_latest_completed_month(decision_at))
    last_month = min(benchmark_months[-1], completed)
    data_end = bu.coverage_end(_month(last_month))
    selection = select_listings(symbols, months_by_pair, window_start=window_start, window_end=last_month)
    log.info("%d new listings, %d excluded", len(selection.listings), len(selection.excluded))

    summary = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        bench = download_1h(session, BENCHMARK, month_range(window_start, last_month),
                            out_dir / "1h" / f"{BENCHMARK}.parquet", decision_at=decision_at, pool=pool)
        if bench is None:
            raise DataQualityError("benchmark has no candles")
        for pair, first in sorted(selection.listings.items()):
            months = month_range(first, min(_shift(first, MONTHS_AFTER_FIRST), last_month))
            info = download_1h(session, pair, months, out_dir / "1h" / f"{pair}.parquet",
                               decision_at=decision_at, pool=pool)
            if info is None:
                selection.excluded[pair] = "NO_CANDLES"
                continue
            summary[pair] = {**info, "first_month": first}
            log.info("%s: %d bars from %s", pair, info["bars"], first)

    manifest = {
        "schema": "binance-listings/v1",
        "generated_at": decision_at,
        "window": [window_start, last_month],
        "data_end": data_end,
        "benchmark": {BENCHMARK: bench},
        "spot_pairs_listed": len(symbols),
        "usdt_pairs": len(usdt),
        "listings": summary,
        "excluded": dict(sorted(selection.excluded.items())),
        "migration_check": identity.verify_migrations(months_by_pair),
        "can_authorize_trade": False,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=1), "utf-8")
    tmp.replace(out_dir / "manifest.json")
    return manifest


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Download the survivorship-free new-listing dataset.")
    parser.add_argument("--out", type=Path, default=Path("research/data/listings"))
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    manifest = build_listings(args.out)
    print(json.dumps({k: v for k, v in manifest.items() if k not in ("listings", "excluded")}, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
