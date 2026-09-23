"""Binance historical data adapter via data.binance.vision monthly zips.

The live ``fapi.binance.com`` and ``api.binance.com`` endpoints reject
requests from many cloud provider IP ranges (GitHub Actions runners
included) with HTTP 451, which broke the Faz A workflow when we tried to
run it in CI. Binance separately publishes the same historical klines and
funding-rate series as static zip files at ``data.binance.vision``; that
host is CDN-fronted and has no geographic ACL, so it stays reachable from
any Actions runner.

This module wraps the daily/monthly zip archive layout with the same
``download_symbol`` + parquet write contract that ``binance_perp.py`` uses,
so ``walk_forward`` continues to read exactly the same parquet files and
the strategy code doesn't have to change. Live REST access still lives in
``binance_perp.py`` for developers who can reach the API directly.

Point-in-time invariants (AGENTS.md §1, §5) are preserved: candles are
parsed through the same ``Candle`` constructor as the live path, so open
candles, malformed OHLC, or missing ``close_time`` values are rejected the
same way regardless of which transport supplied the data.

Order authority: none (AGENTS.md §4). This is a read-only research
adapter.
"""

from __future__ import annotations

import calendar
import io
import logging
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import requests

from trading.data.binance_perp import (
    DEFAULT_TIMEFRAMES,
    Candle,
    DataQualityError,
    FundingRow,
    Timeframe,
    write_candle_parquet,
    write_funding_parquet,
)


log = logging.getLogger(__name__)

BASE_URL = "https://data.binance.vision"


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------


def monthly_kline_url(symbol: str, timeframe: Timeframe, year: int, month: int) -> str:
    return (
        f"{BASE_URL}/data/futures/um/monthly/klines/"
        f"{symbol.upper()}/{timeframe.value}/"
        f"{symbol.upper()}-{timeframe.value}-{year:04d}-{month:02d}.zip"
    )


def monthly_funding_url(symbol: str, year: int, month: int) -> str:
    return (
        f"{BASE_URL}/data/futures/um/monthly/fundingRate/"
        f"{symbol.upper()}/"
        f"{symbol.upper()}-fundingRate-{year:04d}-{month:02d}.zip"
    )


# ---------------------------------------------------------------------------
# Month iteration
# ---------------------------------------------------------------------------


def _iter_months(
    *,
    start_year: int,
    start_month: int,
    end_year: int,
    end_month: int,
) -> Iterator[tuple[int, int]]:
    if (start_year, start_month) > (end_year, end_month):
        return
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        yield year, month
        month += 1
        if month > 12:
            year += 1
            month = 1


def months_ending_at(
    *,
    end_year: int,
    end_month: int,
    lookback_months: int,
) -> list[tuple[int, int]]:
    """Return ``lookback_months`` (year, month) pairs ending at ``(end_year, end_month)``.

    The list is chronological. Missing months (e.g. a symbol listed only 6
    months ago) are still yielded; the download step returns None for those
    and the caller filters them out.
    """

    if lookback_months <= 0:
        raise ValueError("lookback_months must be positive")
    pairs: list[tuple[int, int]] = []
    y, m = end_year, end_month
    for _ in range(lookback_months):
        pairs.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(pairs))


# ---------------------------------------------------------------------------
# HTTP + zip extraction
# ---------------------------------------------------------------------------


def _fetch_zip(
    url: str,
    *,
    session: requests.Session,
    timeout: int = 30,
) -> bytes | None:
    """Download a zip archive. Returns None on 404 (month not published yet)."""

    response = session.get(url, timeout=timeout)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def _extract_single_csv(zip_bytes: bytes) -> str:
    """Extract the single CSV inside a Binance vision zip. Case-insensitive."""

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = [name for name in archive.namelist() if name.endswith(".csv")]
        if not names:
            raise DataQualityError("no CSV in vision zip")
        with archive.open(names[0]) as csv_file:
            return csv_file.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


def _split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.split(",")]


def _is_kline_header(row: list[str]) -> bool:
    if not row:
        return False
    first = row[0].lower()
    return not first.lstrip("-").isdigit()


def parse_kline_csv(csv_text: str, *, decision_at: int) -> list[Candle]:
    """Parse a Binance-vision monthly klines CSV into ``Candle`` rows.

    Columns (no header in older dumps, header in newer):
        open_time, open, high, low, close, volume, close_time,
        quote_volume, count, taker_buy_volume, taker_buy_quote_volume, ignore
    """

    lines = [ln for ln in csv_text.splitlines() if ln.strip()]
    if not lines:
        return []
    rows = [_split_row(line) for line in lines]
    if _is_kline_header(rows[0]):
        rows = rows[1:]
    candles: list[Candle] = []
    for row in rows:
        if len(row) < 8:
            continue
        try:
            open_time_ms = int(row[0])
            close_time_ms = int(row[6])
            candle = Candle(
                open_time=open_time_ms // 1000,
                close_time=close_time_ms // 1000,
                available_at=close_time_ms // 1000,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                quote_volume=float(row[7]),
            )
        except (ValueError, IndexError, DataQualityError):
            # Malformed row; skip rather than fail the whole month.
            continue
        if candle.close_time <= decision_at:
            candles.append(candle)
    return candles


def _is_funding_header(row: list[str]) -> bool:
    if not row:
        return False
    first = row[0].lower()
    return not first.lstrip("-").isdigit()


def parse_funding_csv(
    csv_text: str,
    *,
    symbol: str,
    decision_at: int,
) -> list[FundingRow]:
    """Parse a Binance-vision monthly fundingRate CSV.

    Two known layouts:
    - Older: ``calc_time, funding_interval_hours, last_funding_rate``
    - Newer: ``calc_time, last_funding_rate, funding_interval_hours``

    We detect by whether the second column has decimal ``.``.
    """

    lines = [ln for ln in csv_text.splitlines() if ln.strip()]
    if not lines:
        return []
    rows = [_split_row(line) for line in lines]
    if _is_funding_header(rows[0]):
        header = [h.lower() for h in rows[0]]
        rows = rows[1:]
        rate_idx = 2 if "funding_interval_hours" in header[1] else 1
    else:
        # No header — inspect first data row to pick the rate column.
        rate_idx = 1
        if len(rows[0]) >= 3:
            try:
                float(rows[0][1])
                second_has_dot = "." in rows[0][1]
                second_int_like = rows[0][1].isdigit()
                if second_int_like and not second_has_dot:
                    rate_idx = 2
            except (ValueError, IndexError):
                rate_idx = 2
    parsed: list[FundingRow] = []
    for row in rows:
        if len(row) <= rate_idx:
            continue
        try:
            calc_time_ms = int(row[0])
            funding_time = calc_time_ms // 1000
            funding_rate = float(row[rate_idx])
            parsed.append(
                FundingRow(
                    symbol=symbol.upper(),
                    funding_time=funding_time,
                    available_at=funding_time,
                    funding_rate=funding_rate,
                )
            )
        except (ValueError, IndexError, DataQualityError):
            continue
    parsed.sort(key=lambda item: item.funding_time)
    return [item for item in parsed if item.visible_at(decision_at)]


# ---------------------------------------------------------------------------
# High-level fetchers
# ---------------------------------------------------------------------------


def _session(timeout: int) -> requests.Session:
    session = requests.Session()
    session.headers.setdefault(
        "User-Agent", "signal-bot-research/1.0 (vision-adapter)"
    )
    return session


def _dedupe_sorted(candles: Iterable[Candle]) -> tuple[Candle, ...]:
    seen: set[int] = set()
    out: list[Candle] = []
    for candle in sorted(candles, key=lambda item: item.open_time):
        if candle.open_time in seen:
            continue
        seen.add(candle.open_time)
        out.append(candle)
    return tuple(out)


def fetch_klines_months(
    symbol: str,
    timeframe: Timeframe,
    months: Sequence[tuple[int, int]],
    *,
    decision_at: int,
    session: requests.Session | None = None,
    max_workers: int = 8,
) -> tuple[Candle, ...]:
    """Fetch klines across a set of (year, month) pairs concurrently.

    Months not yet published on the vision CDN return 404 and are skipped.
    The caller must ensure at least one month yields data or the empty
    return will surface downstream.
    """

    session = session or _session(timeout=30)
    urls = [
        (year, month, monthly_kline_url(symbol, timeframe, year, month))
        for year, month in months
    ]
    all_candles: list[Candle] = []

    def _download(item: tuple[int, int, str]) -> list[Candle]:
        year, month, url = item
        try:
            zip_bytes = _fetch_zip(url, session=session)
        except requests.RequestException as exc:
            log.warning("vision klines fetch failed %s: %s", url, exc)
            return []
        if zip_bytes is None:
            return []
        try:
            csv_text = _extract_single_csv(zip_bytes)
        except (zipfile.BadZipFile, DataQualityError) as exc:
            log.warning("vision klines zip malformed %s-%02d: %s", year, month, exc)
            return []
        return parse_kline_csv(csv_text, decision_at=decision_at)

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        for candles in executor.map(_download, urls):
            all_candles.extend(candles)
    return _dedupe_sorted(all_candles)


def fetch_funding_months(
    symbol: str,
    months: Sequence[tuple[int, int]],
    *,
    decision_at: int,
    session: requests.Session | None = None,
    max_workers: int = 4,
) -> tuple[FundingRow, ...]:
    session = session or _session(timeout=30)
    urls = [
        (year, month, monthly_funding_url(symbol, year, month))
        for year, month in months
    ]
    all_rows: list[FundingRow] = []

    def _download(item: tuple[int, int, str]) -> list[FundingRow]:
        year, month, url = item
        try:
            zip_bytes = _fetch_zip(url, session=session)
        except requests.RequestException as exc:
            log.warning("vision funding fetch failed %s: %s", url, exc)
            return []
        if zip_bytes is None:
            return []
        try:
            csv_text = _extract_single_csv(zip_bytes)
        except (zipfile.BadZipFile, DataQualityError) as exc:
            log.warning("vision funding zip malformed %s-%02d: %s", year, month, exc)
            return []
        return parse_funding_csv(csv_text, symbol=symbol, decision_at=decision_at)

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        for rows in executor.map(_download, urls):
            all_rows.extend(rows)
    seen: set[int] = set()
    out: list[FundingRow] = []
    for row in sorted(all_rows, key=lambda item: item.funding_time):
        if row.funding_time in seen:
            continue
        seen.add(row.funding_time)
        out.append(row)
    return tuple(out)


# ---------------------------------------------------------------------------
# Symbol-level download (parquet contract)
# ---------------------------------------------------------------------------


DEFAULT_LOOKBACK_MONTHS = 36  # ~3 years — balances history depth with runtime


def _latest_completed_month(now: int) -> tuple[int, int]:
    """Return the most recent fully-elapsed UTC month at ``now``.

    ``data.binance.vision`` publishes a month's zip only after it closes,
    so callers must not request the current month.
    """

    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    if dt.month == 1:
        return dt.year - 1, 12
    return dt.year, dt.month - 1


def download_symbol(
    symbol: str,
    *,
    out_dir: Path,
    decision_at: int | None = None,
    timeframes: Sequence[Timeframe] = DEFAULT_TIMEFRAMES,
    lookback_months: int = DEFAULT_LOOKBACK_MONTHS,
    session: requests.Session | None = None,
) -> dict[str, Path]:
    """Fetch klines + funding for ``symbol`` and persist parquet files.

    The output layout matches ``binance_perp.download_symbol`` so
    ``walk_forward`` reads the same files regardless of which adapter
    produced them.
    """

    decision_at = int(decision_at or time.time())
    session = session or _session(timeout=30)
    end_year, end_month = _latest_completed_month(decision_at)
    months = months_ending_at(
        end_year=end_year,
        end_month=end_month,
        lookback_months=lookback_months,
    )
    written: dict[str, Path] = {}
    for timeframe in timeframes:
        candles = fetch_klines_months(
            symbol,
            timeframe,
            months,
            decision_at=decision_at,
            session=session,
        )
        if not candles:
            raise DataQualityError(
                f"no candles returned for {symbol} {timeframe.value} "
                f"across {len(months)} months (vision CDN may not carry this pair)"
            )
        path = out_dir / f"{symbol.upper()}_klines_{timeframe.value}.parquet"
        write_candle_parquet(candles, path)
        written[f"klines_{timeframe.value}"] = path
        log.info(
            "vision wrote %d %s candles to %s (last close %s)",
            len(candles),
            timeframe.value,
            path,
            candles[-1].close_time,
        )
    funding = fetch_funding_months(
        symbol,
        months,
        decision_at=decision_at,
        session=session,
    )
    if funding:
        funding_path = out_dir / f"{symbol.upper()}_funding.parquet"
        write_funding_parquet(funding, funding_path)
        written["funding"] = funding_path
        log.info("vision wrote %d funding rows to %s", len(funding), funding_path)
    return written


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Download Binance USDⓈ-M perpetual historical data via "
            "data.binance.vision monthly zip dumps."
        ),
    )
    parser.add_argument("symbol", help="e.g. BTCUSDT, ETHUSDT, SOLUSDT")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("research/data/binance_perp"),
        help="output directory for parquet files (matches live adapter)",
    )
    parser.add_argument(
        "--lookback-months",
        type=int,
        default=DEFAULT_LOOKBACK_MONTHS,
        help="how many months of history to pull (default 36 = ~3 years)",
    )
    parser.add_argument(
        "--decision-at",
        type=int,
        default=None,
        help="UTC seconds; defaults to current time",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    paths = download_symbol(
        args.symbol,
        out_dir=args.out,
        decision_at=args.decision_at,
        lookback_months=args.lookback_months,
    )
    for name, path in paths.items():
        print(f"{name}\t{path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
