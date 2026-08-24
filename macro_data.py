"""Point-in-time macro data primitives.

This module acquires, normalizes and archives macro observations while
preserving when the bot could actually know a value. It deliberately contains
no trading score or order authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse
from xml.etree import ElementTree

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
        if not math.isfinite(float(self.value)):
            raise ValueError("macro observation value must be finite")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return _utc()


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
        as_of = _utc(as_of)
        series_id = FRED_SERIES[logical_series]
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 20,
            # Vintage boundary: ask what FRED knew on the requested date.
            "realtime_start": as_of.date().isoformat(),
            "realtime_end": as_of.date().isoformat(),
            # Observation boundary: never allow a future-dated observation to
            # enter a historical/live point-in-time query even if a provider
            # changes its response semantics.
            "observation_end": as_of.date().isoformat(),
        }
        response = self.session.get(self.base_url, params=params, timeout=15)
        response.raise_for_status()
        rows = [row for row in response.json().get("observations", []) if row.get("value") not in (None, ".")]
        if not rows:
            raise RuntimeError(f"no point-in-time FRED observation for {series_id}")
        row = rows[0]
        observation_time = _parse_time(f"{row['date']}T00:00:00+00:00")
        if observation_time.date() > as_of.date():
            raise RuntimeError(f"future-dated FRED observation for {series_id}")
        ingested = _utc_now()
        obs = MacroObservation(
            provider="fred",
            series=logical_series,
            source_id=series_id,
            observation_time=observation_time.isoformat(),
            # FRED daily observations do not expose a trustworthy intraday
            # publication timestamp for every series. In live acquisition the
            # only defensible availability timestamp is when we actually saw it.
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
    """Bank of Japan public API adapter using only verified series codes."""

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
                date = normalized.get("DATE") or normalized.get("TIME") or normalized.get("PERIOD") or normalized.get("TIME_PERIOD")
                value = normalized.get("VALUE")
                if date is not None and value not in (None, "", "NA", "N.A.", "-"):
                    try:
                        numeric = float(str(value).replace(",", ""))
                        if math.isfinite(numeric):
                            rows.append((str(date), numeric, str(unit) if unit else None))
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
        as_of = _utc(as_of)
        db, code = BOJ_SERIES[logical_series]
        start = (as_of.replace(day=1) - timedelta(days=1)).strftime("%Y%m")
        end = as_of.strftime("%Y%m")
        response = self.session.get(
            self.base_url,
            params={"format": "json", "lang": "en", "db": db, "code": code, "startDate": start, "endDate": end},
            timeout=15,
        )
        response.raise_for_status()
        rows = self._find_value_rows(response.json())
        if not rows:
            raise RuntimeError(f"no BOJ value rows for {db}:{code}")
        normalized = sorted((self._boj_date_to_iso(d), v, u) for d, v, u in rows)
        # Critical point-in-time guard: a month-bounded BOJ response can contain
        # observations after an historical as_of date. Those rows are invisible.
        visible = [row for row in normalized if _parse_time(row[0]).date() <= as_of.date()]
        if not visible:
            raise RuntimeError(f"no point-in-time BOJ observation for {db}:{code}")
        observation_time, value, unit = visible[-1]
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
class TreasuryBuybackOperation:
    announcement_time: str
    operation_time: str
    settlement_date: str
    operation_type: str
    security_bucket: str
    max_purchase_usd: float
    source_url: str
    ingested_time: str

    def validate(self) -> None:
        announced = _parse_time(self.announcement_time)
        operated = _parse_time(self.operation_time)
        ingested = _parse_time(self.ingested_time)
        if announced > ingested:
            raise ValueError("future Treasury announcement supplied")
        if operated < announced:
            raise ValueError("Treasury operation precedes announcement")
        if not math.isfinite(float(self.max_purchase_usd)) or self.max_purchase_usd < 0:
            raise ValueError("invalid Treasury buyback capacity")


class TreasuryBuybackAdapter:
    """Parse official Treasury Quarterly Refunding buyback schedule XML.

    Schedule capacity is *not* a completed purchase and must never be scored as
    realized liquidity. The source URL is supplied by configuration so a
    quarterly document can be pinned and archived rather than silently changing
    when Treasury rotates its 'most recent' URL.
    """

    allowed_hosts = ("home.treasury.gov",)

    def __init__(self, schedule_url: str, session: requests.Session | None = None):
        self._validate_source(schedule_url)
        self.schedule_url = schedule_url
        self.session = session or requests.Session()

    @classmethod
    def _validate_source(cls, source_url: str) -> None:
        parsed = urlparse(source_url)
        if parsed.scheme != "https" or parsed.hostname not in cls.allowed_hosts:
            raise ValueError("Treasury schedule must use official home.treasury.gov source")

    @staticmethod
    def _text(node: ElementTree.Element, names: tuple[str, ...]) -> str | None:
        wanted = {name.lower().replace("_", "").replace("-", "").replace(" ", "") for name in names}
        for child in node.iter():
            tag = child.tag.split("}")[-1].lower().replace("_", "").replace("-", "").replace(" ", "")
            if tag in wanted and child.text and child.text.strip():
                return child.text.strip()
        return None

    @staticmethod
    def _money(value: str) -> float:
        cleaned = value.lower().replace("$", "").replace(",", "").strip()
        multiplier = 1.0
        if "billion" in cleaned or cleaned.endswith("b"):
            multiplier = 1_000_000_000.0
        elif "million" in cleaned or cleaned.endswith("m"):
            multiplier = 1_000_000.0
        number = "".join(ch for ch in cleaned if ch.isdigit() or ch in ".-")
        if not number:
            raise ValueError(f"unrecognized Treasury amount: {value}")
        amount = float(number) * multiplier
        if not math.isfinite(amount):
            raise ValueError(f"non-finite Treasury amount: {value}")
        return amount

    @staticmethod
    def _date(value: str) -> datetime:
        value = value.strip().replace("*", "")
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        raise ValueError(f"unrecognized Treasury date: {value}")

    @classmethod
    def parse_xml(cls, xml_text: str, *, source_url: str, ingested_at: datetime) -> tuple[TreasuryBuybackOperation, ...]:
        cls._validate_source(source_url)
        ingested_at = _utc(ingested_at)
        root = ElementTree.fromstring(xml_text)
        operations: list[TreasuryBuybackOperation] = []
        for node in root.iter():
            announcement = cls._text(node, ("AnnouncementDate", "Announcement Date"))
            operation = cls._text(node, ("OperationDate", "Operation Date"))
            settlement = cls._text(node, ("SettlementDate", "Settlement Date"))
            operation_type = cls._text(node, ("OperationType", "Operation Type"))
            bucket = cls._text(node, ("SecurityTypeMaturityRange", "SecurityType", "Security Type & Maturity Range"))
            maximum = cls._text(node, ("MaximumPurchaseAmount", "MaxPurchaseAmount", "Maximum Purchase Amount"))
            if not all((announcement, operation, settlement, operation_type, bucket, maximum)):
                continue
            announced_dt = cls._date(announcement)
            operated_dt = cls._date(operation)
            item = TreasuryBuybackOperation(
                announcement_time=announced_dt.isoformat(),
                operation_time=operated_dt.isoformat(),
                settlement_date=settlement.replace("*", ""),
                operation_type=operation_type,
                security_bucket=bucket,
                max_purchase_usd=cls._money(maximum),
                source_url=source_url,
                ingested_time=ingested_at.isoformat(),
            )
            item.validate()
            operations.append(item)
        unique = {(x.announcement_time, x.operation_time, x.operation_type, x.security_bucket, x.max_purchase_usd): x for x in operations}
        if not unique:
            raise RuntimeError("no Treasury buyback operations parsed from official schedule")
        return tuple(sorted(unique.values(), key=lambda x: (x.operation_time, x.security_bucket)))

    def fetch_schedule(self, *, as_of: datetime | None = None) -> tuple[TreasuryBuybackOperation, ...]:
        as_of = _utc(as_of)
        response = self.session.get(self.schedule_url, timeout=20)
        response.raise_for_status()
        return self.parse_xml(response.text, source_url=self.schedule_url, ingested_at=as_of)


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
    """Point-in-time coverage report; future-available rows are invisible."""
    as_of = _utc(as_of)
    required = tuple(dict.fromkeys(required_series))
    latest: dict[str, MacroObservation] = {}
    for obs in observations:
        obs.validate()
        # A valid archive row may have become available after the requested
        # historical cut. It must not leak backwards into that cut.
        if _parse_time(obs.available_time) > as_of:
            continue
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
    return MacroCoverage(required, present, missing, tuple(stale), not missing and not stale)


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
            if line.strip():
                row = MacroObservation(**json.loads(line))
                row.validate()
                rows.append(row)
        return tuple(rows)
