"""Historical research backfill for macro/BTC replay.

This module is deliberately separate from the live first-seen archive. Historical
FRED/ALFRED records have date-level vintage metadata, not trustworthy intraday
publication timestamps for every series. We therefore use a conservative
availability convention: a vintage dated D becomes usable at 00:00 UTC on D+1.
That may delay information, but it cannot manufacture same-day alpha.

Backfilled observations use provider ``fred_alfred_reconstruction`` and MUST be
stored in a separate research archive. They must never be appended to the live
``macro_observations.jsonl`` first-seen archive.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import math
import os
from typing import Iterable

import requests

from macro_data import FRED_SERIES, MacroObservation
from macro_replay import PriceBar


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return _utc(value).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def conservative_date_availability(vintage_date: str | date) -> datetime:
    """Date-only ALFRED vintage becomes visible at next UTC midnight."""
    day = _date(vintage_date)
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone.utc)


class FredRevisionBackfill:
    """Fetch ALFRED new/revised vintages with output_type=3.

    output_type=3 returns only observations that are new or revised on each
    vintage date. This is sufficient to reconstruct the information set through
    time without one API request per trading day.
    """

    base_url = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        self.api_key = api_key or os.getenv("FRED_API_KEY")
        self.session = session or requests.Session()

    def fetch_series(
        self,
        logical_series: str,
        *,
        start: str | date | datetime,
        end: str | date | datetime,
        page_limit: int = 10000,
    ) -> tuple[MacroObservation, ...]:
        if not self.api_key:
            raise RuntimeError("FRED_API_KEY is required")
        if logical_series not in FRED_SERIES:
            raise KeyError(f"unsupported FRED series: {logical_series}")
        if page_limit < 1:
            raise ValueError("page_limit must be positive")
        start_day, end_day = _date(start), _date(end)
        if end_day < start_day:
            raise ValueError("backfill end precedes start")

        series_id = FRED_SERIES[logical_series]
        offset = 0
        parsed: list[MacroObservation] = []
        while True:
            params = {
                "series_id": series_id,
                "api_key": self.api_key,
                "file_type": "json",
                "output_type": 3,
                "sort_order": "asc",
                "realtime_start": start_day.isoformat(),
                "realtime_end": end_day.isoformat(),
                "observation_start": (start_day - timedelta(days=14)).isoformat(),
                "observation_end": end_day.isoformat(),
                "limit": page_limit,
                "offset": offset,
            }
            response = self.session.get(self.base_url, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
            if int(payload.get("output_type", 3)) != 3:
                raise RuntimeError("unexpected FRED output_type for revision backfill")
            rows = payload.get("observations") or []
            for row in rows:
                if row.get("value") in (None, "."):
                    continue
                vintage = row.get("realtime_start")
                observed = row.get("date")
                if not vintage or not observed:
                    raise RuntimeError("FRED revision row missing date metadata")
                vintage_day = date.fromisoformat(vintage)
                observed_day = date.fromisoformat(observed)
                if not (start_day <= vintage_day <= end_day):
                    continue
                if observed_day > vintage_day:
                    raise RuntimeError("FRED revision claims observation after vintage")
                value = float(row["value"])
                if not math.isfinite(value):
                    raise ValueError("non-finite FRED backfill value")
                available = conservative_date_availability(vintage_day)
                # Research-only reconstruction: ingested_time is intentionally
                # set equal to conservative reconstructed availability so the
                # existing factor visibility guard can replay historical cuts.
                # The provider name makes this synthetic semantic explicit and
                # these rows are never mixed into the live first-seen archive.
                obs = MacroObservation(
                    provider="fred_alfred_reconstruction",
                    series=logical_series,
                    source_id=series_id,
                    observation_time=datetime.combine(observed_day, time.min, tzinfo=timezone.utc).isoformat(),
                    available_time=available.isoformat(),
                    ingested_time=available.isoformat(),
                    value=value,
                    realtime_start=vintage_day.isoformat(),
                    realtime_end=row.get("realtime_end"),
                )
                obs.validate()
                parsed.append(obs)

            count = int(payload.get("count", len(rows)))
            offset += len(rows)
            if not rows or offset >= count:
                break

        unique = {
            (row.series, row.observation_time, row.available_time, float(row.value)): row
            for row in parsed
        }
        return tuple(sorted(unique.values(), key=lambda r: (r.available_time, r.observation_time)))

    def fetch_core(
        self,
        *,
        start: str | date | datetime,
        end: str | date | datetime,
    ) -> tuple[MacroObservation, ...]:
        rows: list[MacroObservation] = []
        for logical_series in FRED_SERIES:
            rows.extend(self.fetch_series(logical_series, start=start, end=end))
        return tuple(sorted(rows, key=lambda r: (r.available_time, r.series, r.observation_time)))


@dataclass(frozen=True)
class BinanceBackfillConfig:
    symbol: str = "BTCUSDT"
    interval: str = "1h"
    limit: int = 1000


class BinanceHourlyBackfill:
    """Public Binance spot OHLC backfill for research labels only."""

    url = "https://api.binance.com/api/v3/klines"
    interval_ms = 60 * 60 * 1000

    def __init__(self, session: requests.Session | None = None, config: BinanceBackfillConfig | None = None):
        self.session = session or requests.Session()
        self.config = config or BinanceBackfillConfig()
        if self.config.interval != "1h":
            raise ValueError("macro replay currently requires 1h bars")
        if not 1 <= self.config.limit <= 1000:
            raise ValueError("Binance limit must be in [1,1000]")

    @staticmethod
    def _iso_from_ms(value: int) -> str:
        return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc).isoformat()

    @classmethod
    def parse_rows(cls, rows: Iterable[list]) -> tuple[PriceBar, ...]:
        parsed: list[PriceBar] = []
        for row in rows:
            if len(row) < 7:
                raise RuntimeError("short Binance kline row")
            bar = PriceBar(
                timestamp=cls._iso_from_ms(int(row[6])),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
            )
            bar.validate()
            parsed.append(bar)
        return tuple(parsed)

    def fetch(
        self,
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[PriceBar, ...]:
        start = _utc(start)
        end = _utc(end)
        if end <= start:
            raise ValueError("price backfill end must follow start")
        cursor = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        bars: list[PriceBar] = []
        while cursor < end_ms:
            response = self.session.get(
                self.url,
                params={
                    "symbol": self.config.symbol,
                    "interval": self.config.interval,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": self.config.limit,
                },
                timeout=30,
            )
            response.raise_for_status()
            batch = self.parse_rows(response.json())
            if not batch:
                break
            bars.extend(batch)
            last_close_ms = int(datetime.fromisoformat(batch[-1].timestamp).timestamp() * 1000)
            next_cursor = last_close_ms + 1
            if next_cursor <= cursor:
                raise RuntimeError("Binance backfill cursor did not advance")
            cursor = next_cursor
            if len(batch) < self.config.limit:
                break

        unique = {bar.timestamp: bar for bar in bars if datetime.fromisoformat(bar.timestamp) <= end}
        return tuple(sorted(unique.values(), key=lambda bar: datetime.fromisoformat(bar.timestamp)))
