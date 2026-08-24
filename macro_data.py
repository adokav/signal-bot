"""Point-in-time macro data primitives.

This module acquires, normalizes and archives macro observations while
preserving when the bot could actually know a value.  It deliberately contains
no trading score or order authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

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

# BOJ's public API manual explicitly documents this daily series code.  More
# Japan/carry series must be added only after their metadata codes are verified
# against /getMetadata; guessing codes is forbidden.
BOJ_SERIES = {
    "boj_call_rate": ("FM01", "STRDCLUCON"),
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
    unit: str | None = None

    def validate(self) -> None:
        observed = _parse_time(self.observation_time)
        available = _parse_time(self.available_time)
        ingested = _parse_time(self.ingested_time)
        if available > ingested:
            raise ValueError("macro observation claims future availability")
        if observed > available:
            raise ValueError("observation time cannot be after available time")
        if not self.provider or not self.series:
            raise ValueError("macro observation requires provider and series")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
        as_of = (as_of or _utc_now()).astimezone(timezone.utc)
        series_id = FRED_SERIES[logical_series]
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 20,
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
        # FRED observations carry an observation date, not a trustworthy
        # intraday publication timestamp for every series.  Live availability
        # is therefore the actual ingestion time. Historical replay must add a
        # release-calendar/vintage layer before these rows may score.
        ingested = _utc_now()
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

    def fetch_core(self, *, as_of: datetime | None = None) -> tuple[MacroObservation, ...]:
        return tuple(self.fetch_latest(name, as_of=as_of) for name in FRED_SERIES)


class BojAdapter:
    """Bank of Japan public time-series API adapter.

    Only verified BOJ series codes are accepted. The BOJ API output can contain
    provider metadata and nested series rows, so parsing is intentionally
    defensive and fails closed if a value/date pair cannot be identified.
    """

    base_url = "https://www.stat-search.boj.or.jp/api/v1/getDataCode"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    @staticmethod
    def _find_value_rows(payload: Any) -> list[tuple[str, float, str | None]]:
        rows: list[tuple[str, float, str | None]] = []

        def walk(node: Any, inherited_unit: str | None = None) -> None:
            if isinstance(node, dict):
                normalized = {str(key).upper(): value for key, value in node.items()}
                unit = normalized.get("UNIT") or normalized.get("UNIT_E") or inherited_unit
                date = (
                    normalized.get("DATE")
                    or normalized.get("TIME")
                    or normalized.get("PERIOD")
                    or normalized.get("TIME_PERIOD")
                )
                value = normalized.get("VALUE")
                if date is not None and value not in (None, "", "NA", "N.A.", "-"):
                    try:
                        rows.append((str(date), float(str(value).replace(",", "")), str(unit) if unit else None))
                    except ValueError:
                        pass
                for child in node.values():
                    walk(child, str(unit) if unit else inherited_unit)
            elif isinstance(node, list):
                for child in node:
                    walk(child, inherited_unit)

        walk(payload)
        return rows

    @staticmethod
    def _boj_date_to_iso(value: str) -> str:
        digits = "".join(char for char in value if char.isdigit())
        if len(digits) >= 8:
            return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}T00:00:00+00:00"
        if len(digits) == 6:
            return f"{digits[:4]}-{digits[4:6]}-01T00:00:00+00:00"
        if len(digits) == 4:
            return f"{digits}-01-01T00:00:00+00:00"
        raise RuntimeError(f"unrecognized BOJ observation date: {value}")

    def fetch_latest(self, logical_series: str, *, as_of: datetime | None = None) -> MacroObservation:
        if logical_series not in BOJ_SERIES:
            raise KeyError(f"unsupported BOJ series: {logical_series}")
        as_of = (as_of or _utc_now()).astimezone(timezone.utc)
        db, code = BOJ_SERIES[logical_series]
        # Daily data may be bounded by month. Ask for the current and previous
        # month so holidays/weekends do not produce false 'missing' errors.
        start = (as_of.replace(day=1) - timedelta(days=1)).strftime("%Y%m")
        end = as_of.strftime("%Y%m")
        response = self.session.get(
            self.base_url,
            params={
                "format": "json",
                "lang": "en",
                "db": db,
                "code": code,
                "startDate": start,
                "endDate": end,
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        rows = self._find_value_rows(payload)
        if not rows:
            raise RuntimeError(f"no BOJ value rows for {db}:{code}")
        # Sort by normalized observation date rather than trusting payload order.
        normalized = sorted((self._boj_date_to_iso(date), value, unit) for date, value, unit in rows)
        observation_time, value, unit = normalized[-1]
        ingested = _utc_now()
        obs = MacroObservation(
            provider="boj",
            series=logical_series,
            source_id=f"{db}:{code}",
            observation_time=observation_time,
            available_time=ingested.isoformat(),
            ingested_time=ingested.isoformat(),
            value=value,
            unit=unit,
        )
        obs.validate()
        return obs


@dataclass(frozen=True)
class MacroCoverage:
    required_series: tuple[str, ...]
    present_series: tuple[str, ...]
    missing_series: tuple[str, ...]
    stale_series: tuple[str, ...]
    healthy: bool


def coverage_report(
    observations: Iterable[MacroObservation],
    *,
    required_series: Iterable[str],
    as_of: datetime | None = None,
    max_age: Mapping[str, timedelta] | None = None,
) -> MacroCoverage:
    """Report coverage without converting missing data into neutral evidence."""
    as_of = (as_of or _utc_now()).astimezone(timezone.utc)
    required = tuple(dict.fromkeys(required_series))
    latest: dict[str, MacroObservation] = {}
    for obs in observations:
        obs.validate()
        old = latest.get(obs.series)
        if old is None or _parse_time(obs.available_time) > _parse_time(old.available_time):
            latest[obs.series] = obs
    present = tuple(name for name in required if name in latest)
    missing = tuple(name for name in required if name not in latest)
    stale: list[str] = []
    ages = dict(max_age or {})
    for name in present:
        threshold = ages.get(name)
        if threshold is not None and as_of - _parse_time(latest[name].available_time) > threshold:
            stale.append(name)
    return MacroCoverage(
        required_series=required,
        present_series=present,
        missing_series=missing,
        stale_series=tuple(stale),
        healthy=not missing and not stale,
    )


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

    def read_all(self) -> tuple[MacroObservation, ...]:
        if not self.path.exists():
            return ()
        rows: list[MacroObservation] = []
        for line in self.path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            row = MacroObservation(**json.loads(line))
            row.validate()
            rows.append(row)
        return tuple(rows)
