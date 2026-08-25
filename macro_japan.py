"""Research-only Japan rates reconstruction for the macro evidence pipeline.

Sources are official: Japan Ministry of Finance JGB constant-maturity yields and
the Bank of Japan Time-Series Data Search API. These historical files/APIs do
not expose a full vintage history for every correction. Therefore observations
from this module are reconstruction inputs, not live first-seen facts, and must
not gain trading authority merely because they are official.

Availability is conservative and calendar-aware without guessing Japanese
holidays: for an observation dated D we use the date of the next published
observation as the next business day, then apply the official release clock.
The final source row is omitted because its next-business-day release date
cannot be inferred from the dataset alone.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta, timezone
import io
import math
from typing import Any

import requests

from macro_data import BOJ_SERIES, BojAdapter, MacroObservation


MOF_JGB_URL = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv"
MOF_JGB_COLUMNS = {
    "jgb_2y": "2Y",
    "jgb_10y": "10Y",
    "jgb_30y": "30Y",
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _day(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return _utc(value).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _release_time(day: date, *, hour_utc: int, minute_utc: int) -> datetime:
    return datetime.combine(day, time(hour_utc, minute_utc), tzinfo=timezone.utc)


class MofJgbBackfill:
    """MOF constant-maturity JGB curve reconstruction.

    MOF states that the rates are based on 3pm market closes and are released
    at 9:30am Japan time on the next business day. We infer that business day
    from the next observation date in the official daily file.
    """

    url = MOF_JGB_URL

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    @classmethod
    def parse_csv(
        cls,
        text: str,
        *,
        start: str | date | datetime,
        end: str | date | datetime,
    ) -> tuple[MacroObservation, ...]:
        start_day, end_day = _day(start), _day(end)
        if end_day < start_day:
            raise ValueError("MOF JGB backfill end precedes start")

        rows = list(csv.reader(io.StringIO(text)))
        header_index = next((i for i, row in enumerate(rows) if row and row[0].strip() == "Date"), None)
        if header_index is None:
            raise RuntimeError("MOF JGB CSV missing Date header")
        header = [cell.strip() for cell in rows[header_index]]
        indexes: dict[str, int] = {}
        for logical, column in MOF_JGB_COLUMNS.items():
            if column not in header:
                raise RuntimeError(f"MOF JGB CSV missing required column: {column}")
            indexes[logical] = header.index(column)

        daily: list[tuple[date, dict[str, float]]] = []
        for raw in rows[header_index + 1 :]:
            if not raw or not raw[0].strip():
                continue
            try:
                observed = datetime.strptime(raw[0].strip(), "%Y/%m/%d").date()
            except ValueError as exc:
                raise RuntimeError(f"invalid MOF JGB date: {raw[0]}") from exc
            values: dict[str, float] = {}
            for logical, idx in indexes.items():
                cell = raw[idx].strip() if idx < len(raw) else ""
                if cell in ("", "-"):
                    continue
                value = float(cell)
                if not math.isfinite(value):
                    raise ValueError("non-finite MOF JGB yield")
                values[logical] = value
            daily.append((observed, values))

        daily.sort(key=lambda item: item[0])
        if any(b[0] <= a[0] for a, b in zip(daily, daily[1:])):
            raise RuntimeError("MOF JGB dates must be strictly increasing")

        parsed: list[MacroObservation] = []
        # Need the next source observation to infer the next business-day release.
        for (observed, values), (next_day, _) in zip(daily, daily[1:]):
            if not (start_day <= observed <= end_day):
                continue
            available = _release_time(next_day, hour_utc=0, minute_utc=30)  # 09:30 JST
            observed_at = datetime.combine(observed, time(15, 0), tzinfo=timezone(timedelta(hours=9))).astimezone(timezone.utc)
            for logical, value in values.items():
                obs = MacroObservation(
                    provider="mof_jgb_reconstruction",
                    series=logical,
                    source_id=f"MOF:JGB:{MOF_JGB_COLUMNS[logical]}",
                    observation_time=observed_at.isoformat(),
                    available_time=available.isoformat(),
                    ingested_time=available.isoformat(),
                    value=value,
                    unit="percent",
                )
                obs.validate()
                parsed.append(obs)
        return tuple(sorted(parsed, key=lambda row: (row.available_time, row.series, row.observation_time)))

    def fetch_core(self, *, start: str | date | datetime, end: str | date | datetime) -> tuple[MacroObservation, ...]:
        response = self.session.get(self.url, timeout=30)
        response.raise_for_status()
        return self.parse_csv(response.text, start=start, end=end)


class BojCallRateBackfill:
    """BOJ uncollateralized overnight call-rate historical reconstruction."""

    base_url = BojAdapter.base_url
    logical_series = "boj_call_rate"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()

    def fetch_core(self, *, start: str | date | datetime, end: str | date | datetime) -> tuple[MacroObservation, ...]:
        start_day, end_day = _day(start), _day(end)
        if end_day < start_day:
            raise ValueError("BOJ call-rate backfill end precedes start")
        db, code = BOJ_SERIES[self.logical_series]
        # Ask slightly beyond end so the final in-range observation can get a
        # next-business-day release timestamp from the following source row.
        query_end = end_day + timedelta(days=10)
        response = self.session.get(
            self.base_url,
            params={
                "format": "json", "lang": "en", "db": db, "code": code,
                "startDate": start_day.strftime("%Y%m"), "endDate": query_end.strftime("%Y%m"),
            },
            timeout=30,
        )
        response.raise_for_status()
        payload: Any = response.json()
        if isinstance(payload, dict) and payload.get("STATUS") not in (None, 200, "200"):
            raise RuntimeError(f"BOJ API returned STATUS={payload.get('STATUS')}")
        value_rows = BojAdapter._find_value_rows(payload)
        normalized: dict[date, tuple[float, str | None]] = {}
        for raw_date, value, unit in value_rows:
            observed = datetime.fromisoformat(BojAdapter._boj_date_to_iso(raw_date)).date()
            normalized[observed] = (float(value), unit)
        ordered = sorted(normalized.items())
        if len(ordered) < 2:
            raise RuntimeError("insufficient BOJ call-rate rows for release reconstruction")

        parsed: list[MacroObservation] = []
        for (observed, (value, unit)), (next_day, _) in zip(ordered, ordered[1:]):
            if not (start_day <= observed <= end_day):
                continue
            if not math.isfinite(value):
                raise ValueError("non-finite BOJ call rate")
            available = _release_time(next_day, hour_utc=1, minute_utc=0)  # around 10:00 JST final result
            obs = MacroObservation(
                provider="boj_call_reconstruction",
                series=self.logical_series,
                source_id=f"{db}:{code}",
                observation_time=datetime.combine(observed, time.min, tzinfo=timezone.utc).isoformat(),
                available_time=available.isoformat(),
                ingested_time=available.isoformat(),
                value=value,
                unit=unit or "percent",
            )
            obs.validate()
            parsed.append(obs)
        return tuple(parsed)


class JapanRatesBackfill:
    """Composite official-source Japan carry research provider."""

    def __init__(self, *, mof: MofJgbBackfill | None = None, boj: BojCallRateBackfill | None = None):
        self.mof = mof or MofJgbBackfill()
        self.boj = boj or BojCallRateBackfill()

    def fetch_core(self, *, start: str | date | datetime, end: str | date | datetime) -> tuple[MacroObservation, ...]:
        rows = list(self.mof.fetch_core(start=start, end=end))
        rows.extend(self.boj.fetch_core(start=start, end=end))
        return tuple(sorted(rows, key=lambda row: (row.available_time, row.series, row.observation_time)))
