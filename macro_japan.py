"""Research-only Japan rates snapshot adapters.

Sources are official: Japan Ministry of Finance JGB constant-maturity yields and
the Bank of Japan Time-Series Data Search API. These public historical files do
not expose a complete vintage/correction history. Therefore a value downloaded
today must never be backdated to its original historical release time for a
point-in-time replay.

All observations emitted by this module use the *actual retrieval time* as both
``available_time`` and ``ingested_time``. Historical ``observation_time`` is
preserved, but a replay cut before the retrieval timestamp cannot see the row.
These snapshots are suitable for descriptive research and for building a true
first-seen archive going forward; they are not retrospective point-in-time
history and must not gain trading authority.
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _day(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return _utc(value).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _validate_boj_candidate_rows(payload: Any) -> None:
    """Fail closed when any BOJ observation-like row is malformed.

    ``BojAdapter._find_value_rows`` is deliberately permissive and skips bad
    leaves. Research ingestion cannot accept that behavior: every dict carrying
    an observation date/time key must also carry a parseable, finite VALUE.
    Otherwise a partially corrupt provider response could be archived as a
    healthy subset.
    """

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            normalized = {str(key).upper(): value for key, value in node.items()}
            date_value = (
                normalized.get("DATE")
                or normalized.get("TIME")
                or normalized.get("PERIOD")
                or normalized.get("TIME_PERIOD")
            )
            if date_value is not None:
                try:
                    BojAdapter._boj_date_to_iso(str(date_value))
                except Exception as exc:
                    raise RuntimeError(f"malformed BOJ observation date: {date_value}") from exc
                if "VALUE" not in normalized:
                    raise RuntimeError("BOJ observation row missing VALUE")
                raw_value = normalized.get("VALUE")
                if raw_value in (None, "", "NA", "N.A.", "-"):
                    raise RuntimeError("malformed BOJ observation value")
                try:
                    numeric = float(str(raw_value).replace(",", ""))
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(f"malformed BOJ observation value: {raw_value}") from exc
                if not math.isfinite(numeric):
                    raise RuntimeError("non-finite BOJ observation value")
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(payload)


class MofJgbBackfill:
    """Current MOF history snapshot, explicitly *not* vintage-safe history."""

    url = MOF_JGB_URL

    def __init__(self, session: requests.Session | None = None, now=_utc_now):
        self.session = session or requests.Session()
        self.now = now

    @classmethod
    def parse_csv(
        cls,
        text: str,
        *,
        start: str | date | datetime,
        end: str | date | datetime,
        ingested_at: datetime,
    ) -> tuple[MacroObservation, ...]:
        start_day, end_day = _day(start), _day(end)
        if end_day < start_day:
            raise ValueError("MOF JGB backfill end precedes start")
        ingested = _utc(ingested_at)

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

        parsed: list[MacroObservation] = []
        seen_dates: set[date] = set()
        for raw in rows[header_index + 1 :]:
            if not raw or not raw[0].strip():
                continue
            try:
                observed = datetime.strptime(raw[0].strip(), "%Y/%m/%d").date()
            except ValueError as exc:
                raise RuntimeError(f"invalid MOF JGB date: {raw[0]}") from exc
            if observed in seen_dates:
                raise RuntimeError("MOF JGB dates must be unique")
            seen_dates.add(observed)
            if not (start_day <= observed <= end_day):
                continue
            observed_at = datetime.combine(
                observed,
                time(15, 0),
                tzinfo=timezone(timedelta(hours=9)),
            ).astimezone(timezone.utc)
            if observed_at > ingested:
                raise RuntimeError("MOF snapshot contains future observation")
            for logical, idx in indexes.items():
                cell = raw[idx].strip() if idx < len(raw) else ""
                if cell in ("", "-"):
                    continue
                try:
                    value = float(cell)
                except ValueError as exc:
                    raise RuntimeError(f"invalid MOF JGB yield: {cell}") from exc
                if not math.isfinite(value):
                    raise ValueError("non-finite MOF JGB yield")
                obs = MacroObservation(
                    provider="mof_jgb_unversioned_snapshot",
                    series=logical,
                    source_id=f"MOF:JGB:{MOF_JGB_COLUMNS[logical]}",
                    observation_time=observed_at.isoformat(),
                    available_time=ingested.isoformat(),
                    ingested_time=ingested.isoformat(),
                    value=value,
                    unit="percent",
                )
                obs.validate()
                parsed.append(obs)
        return tuple(sorted(parsed, key=lambda row: (row.observation_time, row.series)))

    def fetch_core(self, *, start: str | date | datetime, end: str | date | datetime) -> tuple[MacroObservation, ...]:
        response = self.session.get(self.url, timeout=30)
        response.raise_for_status()
        ingested = _utc(self.now())
        return self.parse_csv(response.text, start=start, end=end, ingested_at=ingested)


class BojCallRateBackfill:
    """Current BOJ call-rate history snapshot, not a vintage-safe reconstruction."""

    base_url = BojAdapter.base_url
    logical_series = "boj_call_rate"

    def __init__(self, session: requests.Session | None = None, now=_utc_now):
        self.session = session or requests.Session()
        self.now = now

    def fetch_core(self, *, start: str | date | datetime, end: str | date | datetime) -> tuple[MacroObservation, ...]:
        start_day, end_day = _day(start), _day(end)
        if end_day < start_day:
            raise ValueError("BOJ call-rate backfill end precedes start")
        db, code = BOJ_SERIES[self.logical_series]
        response = self.session.get(
            self.base_url,
            params={
                "format": "json",
                "lang": "en",
                "db": db,
                "code": code,
                "startDate": start_day.strftime("%Y%m"),
                "endDate": end_day.strftime("%Y%m"),
            },
            timeout=30,
        )
        response.raise_for_status()
        payload: Any = response.json()
        if isinstance(payload, dict) and payload.get("STATUS") not in (None, 200, "200"):
            raise RuntimeError(f"BOJ API returned STATUS={payload.get('STATUS')}")
        _validate_boj_candidate_rows(payload)
        value_rows = BojAdapter._find_value_rows(payload)
        if not value_rows:
            raise RuntimeError("no BOJ call-rate rows")

        ingested = _utc(self.now())
        normalized: dict[date, tuple[float, str | None]] = {}
        for raw_date, value, unit in value_rows:
            observed = datetime.fromisoformat(BojAdapter._boj_date_to_iso(raw_date)).date()
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError("non-finite BOJ call rate")
            prior = normalized.get(observed)
            candidate = (numeric, unit)
            if prior is not None and prior != candidate:
                raise RuntimeError(f"conflicting BOJ observations for {observed.isoformat()}")
            normalized[observed] = candidate

        parsed: list[MacroObservation] = []
        for observed, (value, unit) in sorted(normalized.items()):
            if not (start_day <= observed <= end_day):
                continue
            observed_at = datetime.combine(observed, time.min, tzinfo=timezone.utc)
            if observed_at > ingested:
                raise RuntimeError("BOJ snapshot contains future observation")
            obs = MacroObservation(
                provider="boj_call_unversioned_snapshot",
                series=self.logical_series,
                source_id=f"{db}:{code}",
                observation_time=observed_at.isoformat(),
                available_time=ingested.isoformat(),
                ingested_time=ingested.isoformat(),
                value=value,
                unit=unit or "percent",
            )
            obs.validate()
            parsed.append(obs)
        if not parsed:
            raise RuntimeError("no in-range BOJ call-rate rows")
        return tuple(parsed)


class JapanRatesBackfill:
    """Composite Japan snapshot provider for descriptive/forward archival research."""

    def __init__(self, *, mof: MofJgbBackfill | None = None, boj: BojCallRateBackfill | None = None):
        self.mof = mof or MofJgbBackfill()
        self.boj = boj or BojCallRateBackfill()

    def fetch_core(self, *, start: str | date | datetime, end: str | date | datetime) -> tuple[MacroObservation, ...]:
        rows = list(self.mof.fetch_core(start=start, end=end))
        rows.extend(self.boj.fetch_core(start=start, end=end))
        return tuple(sorted(rows, key=lambda row: (row.available_time, row.series, row.observation_time)))
