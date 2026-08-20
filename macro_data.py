"""Point-in-time macro data primitives.

No scoring or trading decisions live here.  This module only acquires and
normalizes observations while preserving when the bot could actually know a
value.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Iterable

import requests


FRED_SERIES = {
    "us_2y": "DGS2",
    "us_10y": "DGS10",
    "us_30y": "DGS30",
    "us_10y_real": "DFII10",
    "fed_assets": "WALCL",
    "rrp": "RRPONTSYD",
    "tga": "WTREGEN",
}


@dataclass(frozen=True)
class MacroObservation:
    provider: str
    series: str
    observation_time: str
    available_time: str
    ingested_time: str
    value: float
    realtime_start: str | None = None
    realtime_end: str | None = None
    source_id: str | None = None

    def validate(self) -> None:
        observed = _parse_time(self.observation_time)
        available = _parse_time(self.available_time)
        ingested = _parse_time(self.ingested_time)
        if available > ingested:
            raise ValueError("macro observation claims future availability")
        if observed > available:
            raise ValueError("observation time cannot be after available time")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class FredAdapter:
    """FRED/ALFRED adapter preserving real-time/vintage metadata."""

    base_url = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        self.api_key = api_key or os.getenv("FRED_API_KEY")
        self.session = session or requests.Session()

    def fetch_latest(self, logical_series: str, *, as_of: datetime | None = None) -> MacroObservation:
        if not self.api_key:
            raise RuntimeError("FRED_API_KEY is required")
        if logical_series not in FRED_SERIES:
            raise KeyError(f"unsupported FRED series: {logical_series}")
        as_of = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        series_id = FRED_SERIES[logical_series]
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 20,
            # Ask FRED what was available on this date instead of silently using
            # a later revised history in replay/research.
            "realtime_start": as_of.date().isoformat(),
            "realtime_end": as_of.date().isoformat(),
        }
        response = self.session.get(self.base_url, params=params, timeout=15)
        response.raise_for_status()
        payload = response.json()
        rows = [row for row in payload.get("observations", []) if row.get("value") not in (None, ".")]
        if not rows:
            raise RuntimeError(f"no point-in-time FRED observation for {series_id}")
        row = rows[0]
        # FRED daily observations carry an observation date, not an intraday
        # publication timestamp.  To avoid inventing one, availability is the
        # ingestion timestamp in live mode; historical replay must use a
        # release-calendar/vintage layer before these observations can score.
        ingested = datetime.now(timezone.utc)
        obs = MacroObservation(
            provider="fred",
            series=logical_series,
            source_id=series_id,
            observation_time=f"{row['date']}T00:00:00+00:00",
            available_time=ingested.isoformat(),
            ingested_time=ingested.isoformat(),
            value=float(row["value"]),
            realtime_start=row.get("realtime_start"),
            realtime_end=row.get("realtime_end"),
        )
        obs.validate()
        return obs


class JsonlMacroArchive:
    """Append-only observation archive; never rewrites history."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, observations: Iterable[MacroObservation]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for observation in observations:
                observation.validate()
                handle.write(json.dumps(asdict(observation), sort_keys=True) + "\n")
