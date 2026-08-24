"""Live collection runtime for the point-in-time macro archive.

This module only acquires and archives raw observations. It cannot create a
macro score, change a trading signal, size a position or authorize an order.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from macro_data import BOJ_SERIES, FRED_SERIES, BojAdapter, FredAdapter, JsonlMacroArchive, MacroObservation


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class MacroCollectionStatus:
    attempted_at: str
    appended: int
    fetched_series: tuple[str, ...]
    unchanged_series: tuple[str, ...]
    errors: tuple[str, ...]
    archive_path: str

    @property
    def ok(self) -> bool:
        return bool(self.fetched_series) and not self.errors

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["ok"] = self.ok
        return payload


class MacroCollector:
    """Collect first-seen official observations without duplicating archive rows."""

    def __init__(
        self,
        archive_path: str | Path,
        *,
        fred: FredAdapter | None = None,
        boj: BojAdapter | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.archive = JsonlMacroArchive(archive_path)
        self.fred = fred or FredAdapter()
        self.boj = boj or BojAdapter()
        self.now = now

    @staticmethod
    def _signature(obs: MacroObservation) -> tuple[str, str, str, float, str | None]:
        return (obs.provider, obs.series, obs.observation_time, float(obs.value), obs.source_id)

    def collect_once(self) -> MacroCollectionStatus:
        attempted = self.now().astimezone(timezone.utc)
        existing = self.archive.read_all()
        seen = {self._signature(obs) for obs in existing}
        new_rows: list[MacroObservation] = []
        fetched: list[str] = []
        unchanged: list[str] = []
        errors: list[str] = []

        for logical_series in FRED_SERIES:
            try:
                obs = self.fred.fetch_latest(logical_series, as_of=attempted)
                obs.validate()
                fetched.append(logical_series)
                if self._signature(obs) in seen:
                    unchanged.append(logical_series)
                else:
                    new_rows.append(obs)
                    seen.add(self._signature(obs))
            except Exception as exc:
                errors.append(f"fred:{logical_series}:{type(exc).__name__}:{exc}")

        # BOJ is useful but not allowed to break core FRED collection.
        for logical_series in BOJ_SERIES:
            try:
                obs = self.boj.fetch_latest(logical_series, as_of=attempted)
                obs.validate()
                fetched.append(logical_series)
                if self._signature(obs) in seen:
                    unchanged.append(logical_series)
                else:
                    new_rows.append(obs)
                    seen.add(self._signature(obs))
            except Exception as exc:
                errors.append(f"boj:{logical_series}:{type(exc).__name__}:{exc}")

        if new_rows:
            self.archive.append(new_rows)

        return MacroCollectionStatus(
            attempted_at=attempted.isoformat(),
            appended=len(new_rows),
            fetched_series=tuple(fetched),
            unchanged_series=tuple(unchanged),
            errors=tuple(errors),
            archive_path=str(self.archive.path),
        )
