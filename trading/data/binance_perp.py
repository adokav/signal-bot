"""Point-in-time-safe Binance USDⓈ-M perpetual data adapter.

Provides three data streams:

- klines (open, high, low, close, volume, quote_volume) per timeframe
- funding rate history (accrued every 8 hours)
- open interest history (snapshot every 5 minutes; Binance keeps ~30 days)

Point-in-time invariants (AGENTS.md §1, §5):

- ``open_time`` and ``close_time`` are strict UTC epoch seconds.
- A candle whose ``close_time`` exceeds the caller's ``decision_at`` is
  rejected. Providers occasionally return the currently forming bucket;
  it is discarded here rather than promoted to closed evidence.
- Funding and OI rows carry ``available_time`` which equals the provider's
  event timestamp (Binance publishes the eight-hour funding rate at the
  interval boundary; the row is only visible from that instant forward).

No module in this file may authorize an order (AGENTS.md §4). The adapter
is read-only and side-effect free apart from writing parquet artifacts to
the caller-supplied output directory.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import requests


log = logging.getLogger(__name__)


class DataQualityError(ValueError):
    """Raised when Binance returns data unusable at ``decision_at``."""


class Timeframe(str, Enum):
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

    @property
    def seconds(self) -> int:
        return {
            Timeframe.M5: 300,
            Timeframe.M15: 900,
            Timeframe.H1: 3_600,
            Timeframe.H4: 14_400,
            Timeframe.D1: 86_400,
        }[self]


@dataclass(frozen=True)
class Candle:
    """A closed candle with explicit visibility timestamps."""

    open_time: int
    close_time: int
    available_at: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float

    def __post_init__(self) -> None:
        if self.open_time < 0 or self.close_time <= self.open_time:
            raise DataQualityError("invalid candle time range")
        if self.available_at < self.close_time:
            raise DataQualityError("candle cannot be available before close")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise DataQualityError("OHLC values must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise DataQualityError("OHLC geometry is inconsistent")
        if self.volume < 0 or self.quote_volume < 0:
            raise DataQualityError("volume cannot be negative")

    def visible_at(self, decision_at: int) -> bool:
        return self.close_time <= decision_at and self.available_at <= decision_at


@dataclass(frozen=True)
class FundingRow:
    """A single accrued funding rate observation.

    ``funding_time`` is Binance's ``fundingTime`` field in seconds; the rate
    is applied to positions open at that instant. ``available_at`` matches
    ``funding_time`` because the value is only published at the interval.
    """

    symbol: str
    funding_time: int
    available_at: int
    funding_rate: float

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise DataQualityError("funding row requires symbol")
        if self.funding_time <= 0 or self.available_at < self.funding_time:
            raise DataQualityError("invalid funding timestamps")
        if not (-1.0 < self.funding_rate < 1.0):
            raise DataQualityError("funding rate outside plausible range")

    def visible_at(self, decision_at: int) -> bool:
        return self.available_at <= decision_at


@dataclass(frozen=True)
class OpenInterestRow:
    """A single open-interest snapshot.

    Binance publishes ``openInterestHist`` at five-minute buckets.
    """

    symbol: str
    snapshot_time: int
    available_at: int
    open_interest: float
    open_interest_value: float

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise DataQualityError("OI row requires symbol")
        if self.snapshot_time <= 0 or self.available_at < self.snapshot_time:
            raise DataQualityError("invalid OI timestamps")
        if self.open_interest < 0 or self.open_interest_value < 0:
            raise DataQualityError("OI cannot be negative")

    def visible_at(self, decision_at: int) -> bool:
        return self.available_at <= decision_at


def parse_klines(
    rows: Any,
    *,
    timeframe: Timeframe,
    decision_at: int,
) -> tuple[Candle, ...]:
    """Parse a Binance /fapi/v1/klines payload into visible closed candles.

    Rejects a payload that contains multiple open candles; discards a single
    forming candle rather than falsely promoting it to closed evidence.
    """

    if not isinstance(rows, list) or not rows:
        raise DataQualityError("Binance kline payload is empty or malformed")
    parsed: list[Candle] = []
    discarded_open = 0
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 8:
            raise DataQualityError("malformed Binance kline row")
        try:
            open_time = int(row[0]) // 1000
            close_time = int(row[6]) // 1000
            candle = Candle(
                open_time=open_time,
                close_time=close_time,
                available_at=close_time,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
                quote_volume=float(row[7]),
            )
        except (TypeError, ValueError, IndexError) as exc:
            raise DataQualityError("invalid Binance kline values") from exc
        if not candle.visible_at(decision_at):
            discarded_open += 1
            continue
        parsed.append(candle)
    if not parsed:
        raise DataQualityError("no closed candles in Binance kline payload")
    if discarded_open > 1:
        raise DataQualityError("multiple open candles in kline payload")
    for index in range(1, len(parsed)):
        if parsed[index].open_time <= parsed[index - 1].open_time:
            raise DataQualityError("klines must be strictly chronological")
    max_staleness = timeframe.seconds * 2
    if decision_at - parsed[-1].close_time > max_staleness:
        raise DataQualityError("latest kline is stale")
    return tuple(parsed)


def parse_funding(rows: Any, *, decision_at: int) -> tuple[FundingRow, ...]:
    """Parse ``/fapi/v1/fundingRate`` rows into FundingRow tuples."""

    if not isinstance(rows, list) or not rows:
        raise DataQualityError("funding payload is empty or malformed")
    parsed: list[FundingRow] = []
    for row in rows:
        if not isinstance(row, dict):
            raise DataQualityError("malformed funding row")
        try:
            funding_time = int(row["fundingTime"]) // 1000
            parsed.append(
                FundingRow(
                    symbol=str(row["symbol"]).upper(),
                    funding_time=funding_time,
                    available_at=funding_time,
                    funding_rate=float(row["fundingRate"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DataQualityError("invalid funding row values") from exc
    parsed.sort(key=lambda item: item.funding_time)
    visible = tuple(item for item in parsed if item.visible_at(decision_at))
    if not visible:
        raise DataQualityError("no visible funding rows")
    return visible


def parse_open_interest(rows: Any, *, decision_at: int) -> tuple[OpenInterestRow, ...]:
    """Parse ``/futures/data/openInterestHist`` rows."""

    if not isinstance(rows, list) or not rows:
        raise DataQualityError("OI payload is empty or malformed")
    parsed: list[OpenInterestRow] = []
    for row in rows:
        if not isinstance(row, dict):
            raise DataQualityError("malformed OI row")
        try:
            snapshot_time = int(row["timestamp"]) // 1000
            parsed.append(
                OpenInterestRow(
                    symbol=str(row["symbol"]).upper(),
                    snapshot_time=snapshot_time,
                    available_at=snapshot_time,
                    open_interest=float(row["sumOpenInterest"]),
                    open_interest_value=float(row["sumOpenInterestValue"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DataQualityError("invalid OI row values") from exc
    parsed.sort(key=lambda item: item.snapshot_time)
    visible = tuple(item for item in parsed if item.visible_at(decision_at))
    if not visible:
        raise DataQualityError("no visible OI rows")
    return visible


def last_closed_end_time_ms(*, timeframe: Timeframe, decision_at: int) -> int:
    """Final millisecond of the latest fully closed UTC bucket."""

    if decision_at <= 0:
        raise DataQualityError("invalid decision timestamp")
    bucket_start = (decision_at // timeframe.seconds) * timeframe.seconds
    return bucket_start * 1000 - 1


class BinancePerpetualClient:
    """Thin, read-only Binance USDⓈ-M REST client.

    Uses the public futures API. No authenticated endpoint is called and no
    order path exists here. Retry logic is delegated to requests-level 429/5xx
    handling by the caller; the client itself surfaces network errors.
    """

    def __init__(
        self,
        *,
        base_url: str = "https://fapi.binance.com",
        timeout_seconds: int = 15,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "signal-bot-research/1.0")

    def _get(self, path: str, params: Mapping[str, Any]) -> Any:
        response = self.session.get(
            f"{self.base_url}{path}",
            params=dict(params),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def fetch_klines(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        decision_at: int,
        limit: int = 1000,
    ) -> tuple[Candle, ...]:
        payload = self._get(
            "/fapi/v1/klines",
            {
                "symbol": symbol.upper(),
                "interval": timeframe.value,
                "limit": max(1, min(1500, int(limit))),
                "endTime": last_closed_end_time_ms(
                    timeframe=timeframe, decision_at=decision_at
                ),
            },
        )
        return parse_klines(payload, timeframe=timeframe, decision_at=decision_at)

    def fetch_funding(
        self,
        symbol: str,
        *,
        decision_at: int,
        limit: int = 1000,
    ) -> tuple[FundingRow, ...]:
        payload = self._get(
            "/fapi/v1/fundingRate",
            {
                "symbol": symbol.upper(),
                "limit": max(1, min(1000, int(limit))),
                "endTime": decision_at * 1000,
            },
        )
        return parse_funding(payload, decision_at=decision_at)

    def fetch_open_interest(
        self,
        symbol: str,
        *,
        decision_at: int,
        period: str = "5m",
        limit: int = 500,
    ) -> tuple[OpenInterestRow, ...]:
        payload = self._get(
            "/futures/data/openInterestHist",
            {
                "symbol": symbol.upper(),
                "period": period,
                "limit": max(1, min(500, int(limit))),
                "endTime": decision_at * 1000,
            },
        )
        return parse_open_interest(payload, decision_at=decision_at)


# ---------------------------------------------------------------------------
# Parquet persistence
# ---------------------------------------------------------------------------


def _candle_records(candles: Iterable[Candle]) -> list[dict[str, Any]]:
    return [
        {
            "open_time": candle.open_time,
            "close_time": candle.close_time,
            "available_at": candle.available_at,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
            "quote_volume": candle.quote_volume,
        }
        for candle in candles
    ]


def _funding_records(rows: Iterable[FundingRow]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": row.symbol,
            "funding_time": row.funding_time,
            "available_at": row.available_at,
            "funding_rate": row.funding_rate,
        }
        for row in rows
    ]


def _oi_records(rows: Iterable[OpenInterestRow]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": row.symbol,
            "snapshot_time": row.snapshot_time,
            "available_at": row.available_at,
            "open_interest": row.open_interest,
            "open_interest_value": row.open_interest_value,
        }
        for row in rows
    ]


def write_candle_parquet(candles: Sequence[Candle], path: Path) -> None:
    _write_parquet(_candle_records(candles), path)


def write_funding_parquet(rows: Sequence[FundingRow], path: Path) -> None:
    _write_parquet(_funding_records(rows), path)


def write_oi_parquet(rows: Sequence[OpenInterestRow], path: Path) -> None:
    _write_parquet(_oi_records(rows), path)


def _write_parquet(records: list[dict[str, Any]], path: Path) -> None:
    if not records:
        raise DataQualityError("refuse to persist empty parquet")
    import pandas as pd  # local import — keeps runtime bot free of pandas

    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame.from_records(records)
    frame.to_parquet(path, engine="pyarrow", index=False)


# ---------------------------------------------------------------------------
# CLI: python -m trading.data.binance_perp SYMBOL --out DIR
# ---------------------------------------------------------------------------


DEFAULT_TIMEFRAMES = (Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1)


def download_symbol(
    symbol: str,
    *,
    out_dir: Path,
    decision_at: int | None = None,
    timeframes: Sequence[Timeframe] = DEFAULT_TIMEFRAMES,
    kline_limit: int = 1500,
    funding_limit: int = 1000,
    oi_period: str = "5m",
    oi_limit: int = 500,
    client: BinancePerpetualClient | None = None,
) -> dict[str, Path]:
    """Fetch all Faz A data streams for one symbol and persist to parquet."""

    decision_at = int(decision_at or time.time())
    client = client or BinancePerpetualClient()
    written: dict[str, Path] = {}
    for timeframe in timeframes:
        candles = client.fetch_klines(
            symbol,
            timeframe,
            decision_at=decision_at,
            limit=kline_limit,
        )
        path = out_dir / f"{symbol.upper()}_klines_{timeframe.value}.parquet"
        write_candle_parquet(candles, path)
        written[f"klines_{timeframe.value}"] = path
        log.info(
            "wrote %d candles to %s (last close %s)",
            len(candles),
            path,
            candles[-1].close_time,
        )
    funding = client.fetch_funding(symbol, decision_at=decision_at, limit=funding_limit)
    funding_path = out_dir / f"{symbol.upper()}_funding.parquet"
    write_funding_parquet(funding, funding_path)
    written["funding"] = funding_path
    oi = client.fetch_open_interest(
        symbol,
        decision_at=decision_at,
        period=oi_period,
        limit=oi_limit,
    )
    oi_path = out_dir / f"{symbol.upper()}_open_interest.parquet"
    write_oi_parquet(oi, oi_path)
    written["open_interest"] = oi_path
    return written


def _cli(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Download Binance USDⓈ-M perpetual research data.",
    )
    parser.add_argument("symbol", help="e.g. BTCUSDT, ETHUSDT")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("research/data/binance_perp"),
        help="output directory for parquet files",
    )
    parser.add_argument(
        "--decision-at",
        type=int,
        default=None,
        help="UTC seconds; defaults to current time",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    paths = download_symbol(
        args.symbol,
        out_dir=args.out,
        decision_at=args.decision_at,
    )
    for name, path in paths.items():
        print(f"{name}\t{path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
