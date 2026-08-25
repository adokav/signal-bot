"""Japan rates research adapters.

MOF/BOJ historical endpoints used here are unversioned snapshots: they do not
provide a complete vintage/correction history. Observations are therefore
stamped with the actual retrieval time and are suitable for forward archival
research only; they must not be backdated into retrospective point-in-time
replay.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from io import StringIO
import math
from typing import Any, Callable

import requests

from macro_data import MacroObservation


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _parse_provider_date(value: Any) -> date:
    text = str(value).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise RuntimeError(f"malformed provider observation date: {text!r}")


def _finite_float(value: Any, *, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"malformed {label}") from exc
    if not math.isfinite(parsed):
        raise RuntimeError(f"non-finite {label}")
    return parsed


class MofJgbBackfill:
    """MOF JGB constant-maturity snapshot adapter (forward archival only)."""

    url = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcm.csv"
    tenors = {"2Y": "jgb_2y", "10Y": "jgb_10y", "30Y": "jgb_30y"}

    def __init__(self, session: requests.Session | None = None, now: Callable[[], datetime] = _utc_now):
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
        start_d, end_d = _as_date(start), _as_date(end)
        lines = list(csv.reader(StringIO(text)))
        header_idx = next((i for i, row in enumerate(lines) if row and row[0].strip().lower() == "date"), None)
        if header_idx is None:
            raise RuntimeError("MOF CSV missing Date header")
        header = [cell.strip() for cell in lines[header_idx]]
        indices: dict[str, int] = {}
        for tenor in cls.tenors:
            if tenor not in header:
                raise RuntimeError(f"MOF CSV missing required column {tenor}")
            indices[tenor] = header.index(tenor)

        stamp = ingested_at.astimezone(timezone.utc)
        parsed: list[MacroObservation] = []
        for raw in lines[header_idx + 1 :]:
            if not raw or not raw[0].strip():
                continue
            try:
                obs_date = _parse_provider_date(raw[0])
            except RuntimeError:
                continue
            if obs_date < start_d or obs_date > end_d:
                continue
            if obs_date > stamp.date():
                raise RuntimeError("future observation in MOF snapshot")
            for tenor, logical in cls.tenors.items():
                idx = indices[tenor]
                cell = raw[idx].strip() if idx < len(raw) else ""
                if cell in ("", "-"):
                    raise RuntimeError(f"MOF in-range row missing required tenor {tenor}")
                value = _finite_float(cell, label=f"MOF {tenor} value")
                obs = MacroObservation(
                    provider="mof_jgb_unversioned_snapshot",
                    series=logical,
                    observation_time=datetime.combine(obs_date, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
                    available_time=stamp.isoformat(),
                    ingested_time=stamp.isoformat(),
                    value=value,
                    unit="percent",
                )
                obs.validate()
                parsed.append(obs)
        return tuple(parsed)

    def fetch_core(self, *, start: str | date | datetime, end: str | date | datetime) -> tuple[MacroObservation, ...]:
        response = self.session.get(self.url, timeout=30)
        response.raise_for_status()
        return self.parse_csv(response.text, start=start, end=end, ingested_at=self.now())


class BojAdapter:
    base_url = "https://www.stat-search.boj.or.jp/api/v1/getDataCode"

    @staticmethod
    def _find_value_rows(payload: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                normalized = {str(key).upper(): value for key, value in node.items()}
                if any(key in normalized for key in ("DATE", "TIME", "PERIOD", "TIME_PERIOD")) and "VALUE" in normalized:
                    rows.append(normalized)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(payload)
        return rows


def _validate_boj_observation_rows(payload: Any) -> None:
    """Fail closed on any date-bearing malformed BOJ observation candidate."""

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
                if "VALUE" not in normalized or normalized.get("VALUE") in (None, ""):
                    raise RuntimeError("BOJ observation missing VALUE")
                _parse_provider_date(date_value)
                _finite_float(normalized.get("VALUE"), label="BOJ observation value")
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)


class BojCallRateBackfill:
    """BOJ uncollateralized overnight call-rate unversioned snapshot adapter."""

    base_url = BojAdapter.base_url
    logical_series = "boj_call_rate"

    def __init__(self, session: requests.Session | None = None, now: Callable[[], datetime] = _utc_now):
        self.session = session or requests.Session()
        self.now = now

    def fetch_core(self, *, start: str | date | datetime, end: str | date | datetime) -> tuple[MacroObservation, ...]:
        start_d, end_d = _as_date(start), _as_date(end)
        response = self.session.get(
            self.base_url,
            params={"format": "json", "lang": "en", "code": "FM01'STRD"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        status = payload.get("STATUS") if isinstance(payload, dict) else None
        if status not in (None, 200, "200"):
            raise RuntimeError(f"BOJ provider STATUS={status}")
        _validate_boj_observation_rows(payload)
        value_rows = BojAdapter._find_value_rows(payload)
        if not value_rows:
            raise RuntimeError("no BOJ call-rate rows")

        stamp = self.now().astimezone(timezone.utc)
        parsed: list[MacroObservation] = []
        seen: dict[date, float] = {}
        for row in value_rows:
            raw_date = row.get("DATE") or row.get("TIME") or row.get("PERIOD") or row.get("TIME_PERIOD")
            obs_date = _parse_provider_date(raw_date)
            if obs_date < start_d or obs_date > end_d:
                continue
            if obs_date > stamp.date():
                raise RuntimeError("future observation in BOJ snapshot")
            value = _finite_float(row.get("VALUE"), label="BOJ observation value")
            if obs_date in seen and seen[obs_date] != value:
                raise RuntimeError("conflicting duplicate BOJ observation")
            seen[obs_date] = value
            unit = str(row.get("UNIT") or row.get("UNIT_NAME") or "percent")
            obs = MacroObservation(
                provider="boj_call_unversioned_snapshot",
                series=self.logical_series,
                observation_time=datetime.combine(obs_date, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
                available_time=stamp.isoformat(),
                ingested_time=stamp.isoformat(),
                value=value,
                unit=unit or "percent",
            )
            obs.validate()
            parsed.append(obs)
        if not parsed:
            raise RuntimeError("no in-range BOJ call-rate rows")
        return tuple(parsed)


class JapanRatesBackfill:
    """Composite Japan snapshot provider for descriptive/forward archival research.

    The composite is atomic: both MOF JGB and BOJ call-rate legs must contain
    in-range observations. A partial provider snapshot is unknown, not healthy.
    """

    def __init__(self, *, mof: MofJgbBackfill | None = None, boj: BojCallRateBackfill | None = None):
        self.mof = mof or MofJgbBackfill()
        self.boj = boj or BojCallRateBackfill()

    def fetch_core(self, *, start: str | date | datetime, end: str | date | datetime) -> tuple[MacroObservation, ...]:
        mof_rows = tuple(self.mof.fetch_core(start=start, end=end))
        if not mof_rows:
            raise RuntimeError("Japan composite missing in-range MOF JGB observations")
        boj_rows = tuple(self.boj.fetch_core(start=start, end=end))
        if not boj_rows:
            raise RuntimeError("Japan composite missing in-range BOJ call-rate observations")
        rows = list(mof_rows)
        rows.extend(boj_rows)
        return tuple(sorted(rows, key=lambda row: (row.available_time, row.series, row.observation_time)))
