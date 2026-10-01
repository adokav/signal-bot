"""Survivorship-free Binance spot universe for the Liquid-100 replay.

The live Liquid-100 radar ranks the 100 most traded USDT pairs at each scan.
Replaying it honestly needs every pair that *was* liquid at the time,
including the ones that were later delisted (LUNA, FTT, ...). Downloading
only today's pairs would silently drop the coins that blew up — exactly the
survivorship bias AGENTS.md §1 and spec §16 forbid.

Pipeline (all from data.binance.vision, which keeps delisted pairs):

1. **List** every spot pair ever published (the bucket's S3 listing) and keep
   USDT pairs that are neither stablecoins nor leveraged tokens, using the
   same identity rules as the live radar (``acce_unified.cex``).
2. **Rank daily** by quote volume from monthly 1d klines. A pair becomes a
   *candidate* for a month when it ranked in the top ``rank_limit`` (wider
   than the live 100, so intraday rank changes are not lost) with at least
   ``min_quote_volume`` on some day of that month. This only decides what to
   download; the replay recomputes the real top 100 from trailing 24h
   volume at every step, so no decision sees the future.
3. **Download 15m klines** for candidate months plus one month on each side
   (24h look-back for metrics, up to 72h look-forward for outcomes).

Failure policy (AGENTS.md §2): 404 means "not published" and is fine; any
other error is retried, and a file that still fails aborts the run instead
of leaving a silent hole in the universe.

Research only; no order authority (AGENTS.md §4).
"""

from __future__ import annotations

import json
import logging
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

import requests

from acce_unified.cex import is_leveraged_token, is_stable_or_synthetic
from trading.data.binance_perp import Candle, DataQualityError, Timeframe
from trading.data.binance_vision import (
    _extract_single_csv,
    _latest_completed_month,
    _session,
    months_ending_at,
    monthly_kline_url,
    parse_kline_csv,
)


log = logging.getLogger(__name__)

S3_LIST_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
SPOT_KLINES_PREFIX = "data/spot/monthly/klines/"
QUOTE = "USDT"
MIN_USDT_PAIRS = 300  # far fewer means the listing is broken, not the market
DEFAULT_RANK_LIMIT = 150
DEFAULT_MIN_QUOTE_VOLUME = 1_000_000.0
RETRIES = 4


class DownloadError(RuntimeError):
    """A file could not be fetched after retries; the universe would have a hole."""


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def parse_listing(xml_text: str) -> tuple[list[str], str | None]:
    """Symbols from one S3 ListBucketResult page and the marker for the next page."""

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise DataQualityError("unreadable bucket listing") from exc
    namespace = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
    symbols = []
    for prefix in root.iter(f"{namespace}Prefix"):
        value = (prefix.text or "").strip()
        if value.startswith(SPOT_KLINES_PREFIX) and value.endswith("/") and value != SPOT_KLINES_PREFIX:
            symbols.append(value[len(SPOT_KLINES_PREFIX):-1])
    truncated = (root.findtext(f"{namespace}IsTruncated") or "").strip().lower() == "true"
    next_marker = (root.findtext(f"{namespace}NextMarker") or "").strip() or None
    if truncated and next_marker is None:
        raise DataQualityError("truncated listing without a continuation marker")
    return symbols, (next_marker if truncated else None)


def list_spot_symbols(session: requests.Session, *, max_pages: int = 100) -> list[str]:
    symbols: list[str] = []
    marker: str | None = None
    for _ in range(max_pages):
        params = {"delimiter": "/", "prefix": SPOT_KLINES_PREFIX}
        if marker:
            params["marker"] = marker
        response = _get(session, S3_LIST_URL, params=params)
        if response is None:
            raise DataQualityError("bucket listing not found")
        page, marker = parse_listing(response.text)
        symbols.extend(page)
        if marker is None:
            return sorted(set(symbols))
    raise DataQualityError("bucket listing did not terminate")


@dataclass(frozen=True)
class UniverseSymbols:
    eligible: tuple[str, ...]
    stable: tuple[str, ...]
    leveraged: tuple[str, ...]


def filter_usdt_pairs(symbols: Iterable[str], *, quote: str = QUOTE) -> UniverseSymbols:
    """USDT pairs minus stablecoins and leveraged tokens (live radar identity rules)."""

    pairs = sorted({s.upper() for s in symbols if s.upper().endswith(quote) and len(s) > len(quote)})
    bases = {pair[: -len(quote)] for pair in pairs}
    eligible, stable, leveraged = [], [], []
    for pair in pairs:
        base = pair[: -len(quote)]
        if is_stable_or_synthetic(base):
            stable.append(pair)
        elif is_leveraged_token(base, bases):
            leveraged.append(pair)
        else:
            eligible.append(pair)
    return UniverseSymbols(tuple(eligible), tuple(stable), tuple(leveraged))


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


def _get(session: requests.Session, url: str, *, params=None, sleep: Callable[[float], None] = time.sleep):
    """GET with retries. None on 404; DownloadError when every attempt fails."""

    last = "unknown"
    for attempt in range(RETRIES):
        try:
            response = session.get(url, params=params, timeout=60)
        except requests.RequestException as exc:
            last = type(exc).__name__  # exception text can carry URLs; keep the type only
        else:
            if response.status_code == 404:
                return None
            if response.status_code == 200:
                return response
            last = f"HTTP {response.status_code}"
        sleep(2.0 * (2 ** attempt))
    raise DownloadError(f"{last} after {RETRIES} attempts")


def fetch_month(
    session: requests.Session,
    symbol: str,
    timeframe: Timeframe,
    year: int,
    month: int,
    *,
    decision_at: int,
) -> list[Candle]:
    url = monthly_kline_url(symbol, timeframe, year, month, market="spot")
    response = _get(session, url)
    if response is None:
        return []
    try:
        return parse_kline_csv(_extract_single_csv(response.content), decision_at=decision_at)
    except Exception as exc:  # a corrupt archive is a hole, not an empty month
        raise DownloadError(f"{symbol} {timeframe.value} {year}-{month:02d}: {type(exc).__name__}") from exc


def _day(open_time: int) -> int:
    return open_time - open_time % 86_400


def daily_quote_volumes(
    session: requests.Session,
    symbols: Sequence[str],
    months: Sequence[tuple[int, int]],
    *,
    decision_at: int,
    workers: int = 32,
) -> dict[str, dict[int, float]]:
    """symbol -> {UTC day start: quote volume} from monthly 1d klines."""

    tasks = [(symbol, year, month) for symbol in symbols for year, month in months]

    def run(task):
        symbol, year, month = task
        candles = fetch_month(session, symbol, Timeframe.D1, year, month, decision_at=decision_at)
        return symbol, [(_day(c.open_time), c.quote_volume) for c in candles]

    out: dict[str, dict[int, float]] = defaultdict(dict)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for symbol, days in pool.map(run, tasks):
            out[symbol].update(days)
    return dict(out)


def _month_of(day_start: int) -> tuple[int, int]:
    dt = datetime.fromtimestamp(day_start, tz=timezone.utc)
    return dt.year, dt.month


def _neighbours(month: tuple[int, int]) -> list[tuple[int, int]]:
    year, m = month
    prev = (year - 1, 12) if m == 1 else (year, m - 1)
    nxt = (year + 1, 1) if m == 12 else (year, m + 1)
    return [prev, month, nxt]


def candidate_months(
    daily: Mapping[str, Mapping[int, float]],
    *,
    window: Sequence[tuple[int, int]],
    rank_limit: int = DEFAULT_RANK_LIMIT,
    min_quote_volume: float = DEFAULT_MIN_QUOTE_VOLUME,
) -> dict[str, list[tuple[int, int]]]:
    """Months to download per symbol: candidate months plus one month either side, inside the window."""

    by_day: dict[int, list[tuple[float, str]]] = defaultdict(list)
    for symbol, days in daily.items():
        for day, volume in days.items():
            by_day[day].append((volume, symbol))
    hits: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for day, rows in by_day.items():
        rows.sort(key=lambda row: (-row[0], row[1]))
        for volume, symbol in rows[:rank_limit]:
            if volume >= min_quote_volume:
                hits[symbol].add(_month_of(day))
    allowed = set(window)
    out = {}
    for symbol, months in hits.items():
        expanded = {n for month in months for n in _neighbours(month) if n in allowed}
        out[symbol] = sorted(expanded)
    return dict(sorted(out.items()))


def coverage_end(month: tuple[int, int]) -> int:
    """Last second of a planned month (the end of what was downloaded for a symbol)."""

    year, m = month
    nxt = datetime(year + (m == 12), m % 12 + 1, 1, tzinfo=timezone.utc)
    return int(nxt.timestamp()) - 1


def write_15m(symbol: str, candles: Sequence[Candle], path: Path) -> None:
    import pandas as pd

    frame = pd.DataFrame.from_records([
        {"open_time": c.open_time, "close_time": c.close_time, "open": c.open, "high": c.high,
         "low": c.low, "close": c.close, "volume": c.volume, "quote_volume": c.quote_volume}
        for c in candles
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    frame.to_parquet(tmp, index=False)
    tmp.replace(path)


def download_15m(
    session: requests.Session,
    plan: Mapping[str, Sequence[tuple[int, int]]],
    out_dir: Path,
    *,
    decision_at: int,
    workers: int = 16,
) -> dict[str, dict]:
    """Per-symbol 15m parquet files; returns {symbol: {bars, first_open, last_close}}."""

    summary = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for symbol, months in plan.items():
            parts = pool.map(
                lambda ym, s=symbol: fetch_month(session, s, Timeframe.M15, ym[0], ym[1], decision_at=decision_at),
                months,
            )
            seen: set[int] = set()
            candles = []
            for part in parts:
                for candle in part:
                    if candle.open_time not in seen:
                        seen.add(candle.open_time)
                        candles.append(candle)
            if not candles:
                continue
            candles.sort(key=lambda c: c.open_time)
            write_15m(symbol, candles, out_dir / "15m" / f"{symbol}.parquet")
            summary[symbol] = {
                "bars": len(candles),
                "first_open": candles[0].open_time,
                "last_close": candles[-1].close_time,
                "months": [f"{y:04d}-{m:02d}" for y, m in months],
            }
            log.info("%s: %d bars over %d months", symbol, len(candles), len(months))
    return summary


def build_universe(
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
        # 32 download threads share one session; the default pool keeps 10.
        session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=64))
    end_year, end_month = _latest_completed_month(decision_at)
    window = sorted(months_ending_at(end_year=end_year, end_month=end_month, lookback_months=lookback_months))

    listed = list_spot_symbols(session)
    pairs = filter_usdt_pairs(listed)
    if len(pairs.eligible) + len(pairs.stable) + len(pairs.leveraged) < MIN_USDT_PAIRS:
        raise DataQualityError(f"only {len(pairs.eligible)} USDT pairs listed; listing looks incomplete")
    log.info("listed %d spot pairs, %d eligible USDT pairs", len(listed), len(pairs.eligible))

    daily = daily_quote_volumes(session, pairs.eligible, window, decision_at=decision_at)
    plan = candidate_months(daily, window=window, rank_limit=rank_limit, min_quote_volume=min_quote_volume)
    log.info("%d candidate pairs, %d symbol-months", len(plan), sum(len(v) for v in plan.values()))
    files = download_15m(session, plan, out_dir, decision_at=decision_at)

    # The newest month is often not published yet; nothing can be "delisted"
    # for ending where every pair's data ends.
    data_end = max((info["last_close"] for info in files.values()), default=0)
    stopped = sorted(
        symbol for symbol, info in files.items()
        if info["last_close"] < min(coverage_end(plan[symbol][-1]), data_end) - 2 * 86_400
    )
    manifest = {
        "schema": "liquid-universe/v1",
        "generated_at": decision_at,
        "window": [f"{window[0][0]:04d}-{window[0][1]:02d}", f"{window[-1][0]:04d}-{window[-1][1]:02d}"],
        "rank_limit": rank_limit,
        "min_quote_volume": min_quote_volume,
        "listed_spot_pairs": len(listed),
        "usdt_pairs": {"eligible": len(pairs.eligible), "stable": len(pairs.stable),
                       "leveraged": len(pairs.leveraged)},
        "pairs_with_daily_data": len(daily),
        "candidates": len(plan),
        "downloaded": len(files),
        "data_end": data_end,
        # pairs whose data stops more than two days before the end of what was
        # downloaded for them: delisted or halted. Their presence shows the
        # universe is not built from today's survivors only.
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

    parser = argparse.ArgumentParser(description="Download the survivorship-free Binance spot universe.")
    parser.add_argument("--out", type=Path, default=Path("research/data/liquid_universe"))
    parser.add_argument("--lookback-months", type=int, default=72)
    parser.add_argument("--rank-limit", type=int, default=DEFAULT_RANK_LIMIT)
    parser.add_argument("--min-quote-volume", type=float, default=DEFAULT_MIN_QUOTE_VOLUME)
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    manifest = build_universe(
        args.out, lookback_months=args.lookback_months,
        rank_limit=args.rank_limit, min_quote_volume=args.min_quote_volume,
    )
    print(json.dumps({k: v for k, v in manifest.items() if k != "symbols"}, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
