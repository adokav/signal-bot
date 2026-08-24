"""Deterministic macro factor transforms for research.

This module deliberately does *not* create a macro score, regime label, trade
permission or portfolio weight.  It transforms already archived point-in-time
observations into transparent features whose predictive value must be measured
out of sample before they may influence trading.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Iterable

from macro_data import MacroObservation


CORE_FACTOR_SERIES = (
    "us_2y",
    "us_10y",
    "us_30y",
    "us_10y_real",
    "fed_assets",
    "rrp",
    "tga",
)
OPTIONAL_FACTOR_SERIES = ("boj_call_rate",)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _utc(parsed)


def _finite(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("factor input must be finite")
    return value


def visible_history(
    observations: Iterable[MacroObservation],
    series: str,
    *,
    as_of: datetime,
) -> tuple[MacroObservation, ...]:
    """Return observations actually available by ``as_of``, oldest to newest.

    Repeated vintages for the same observation date are reduced to the latest
    version that was available at the cut.  Rows that were ingested or became
    available later are invisible.
    """
    cut = _utc(as_of)
    by_observation: dict[str, MacroObservation] = {}
    for obs in observations:
        obs.validate()
        if obs.series != series:
            continue
        if _time(obs.available_time) > cut or _time(obs.ingested_time) > cut:
            continue
        if _time(obs.observation_time) > cut:
            continue
        key = obs.observation_time
        prior = by_observation.get(key)
        if prior is None or _time(obs.available_time) > _time(prior.available_time):
            by_observation[key] = obs
    return tuple(sorted(by_observation.values(), key=lambda row: _time(row.observation_time)))


def _latest(history: tuple[MacroObservation, ...]) -> float | None:
    return _finite(history[-1].value) if history else None


def _delta(history: tuple[MacroObservation, ...], lag: int = 1) -> float | None:
    if lag < 1:
        raise ValueError("lag must be positive")
    if len(history) <= lag:
        return None
    return _finite(history[-1].value) - _finite(history[-1 - lag].value)


def _pct_change(history: tuple[MacroObservation, ...], lag: int = 1) -> float | None:
    if lag < 1:
        raise ValueError("lag must be positive")
    if len(history) <= lag:
        return None
    current = _finite(history[-1].value)
    previous = _finite(history[-1 - lag].value)
    if previous == 0:
        return None
    return (current / previous - 1.0) * 100.0


@dataclass(frozen=True)
class MacroFactorSnapshot:
    as_of: str
    factors: dict[str, float | None]
    missing_required_series: tuple[str, ...]
    insufficient_history_factors: tuple[str, ...]
    complete: bool

    def validate(self) -> None:
        _time(self.as_of)
        for value in self.factors.values():
            if value is not None and not math.isfinite(float(value)):
                raise ValueError("macro factor must be finite")
        if self.complete and (self.missing_required_series or self.insufficient_history_factors):
            raise ValueError("complete factor snapshot cannot contain data gaps")


def build_factor_snapshot(
    observations: Iterable[MacroObservation],
    *,
    as_of: datetime,
) -> MacroFactorSnapshot:
    """Create transparent, unit-safe features without assigning bullish weights."""
    cut = _utc(as_of)
    rows = tuple(observations)
    history = {
        name: visible_history(rows, name, as_of=cut)
        for name in CORE_FACTOR_SERIES + OPTIONAL_FACTOR_SERIES
    }

    missing_required = tuple(name for name in CORE_FACTOR_SERIES if not history[name])

    us2 = _latest(history["us_2y"])
    us10 = _latest(history["us_10y"])
    us30 = _latest(history["us_30y"])
    real10 = _latest(history["us_10y_real"])

    factors: dict[str, float | None] = {
        # Yield changes are converted percentage-points -> basis points.
        "us_2y_d1_bp": None if _delta(history["us_2y"], 1) is None else _delta(history["us_2y"], 1) * 100.0,
        "us_10y_d1_bp": None if _delta(history["us_10y"], 1) is None else _delta(history["us_10y"], 1) * 100.0,
        "us_30y_d1_bp": None if _delta(history["us_30y"], 1) is None else _delta(history["us_30y"], 1) * 100.0,
        "us_10y_real_d1_bp": None if _delta(history["us_10y_real"], 1) is None else _delta(history["us_10y_real"], 1) * 100.0,
        "us_2y_d5obs_bp": None if _delta(history["us_2y"], 5) is None else _delta(history["us_2y"], 5) * 100.0,
        "us_10y_d5obs_bp": None if _delta(history["us_10y"], 5) is None else _delta(history["us_10y"], 5) * 100.0,
        "us_30y_d5obs_bp": None if _delta(history["us_30y"], 5) is None else _delta(history["us_30y"], 5) * 100.0,
        "curve_2s10s_bp": None if us2 is None or us10 is None else (us10 - us2) * 100.0,
        "curve_10s30s_bp": None if us10 is None or us30 is None else (us30 - us10) * 100.0,
        "real_10y_level_pct": real10,
        # Balance-sheet series have different native units. Percent changes are
        # intentionally used instead of subtracting incomparable dollar scales.
        "fed_assets_d1_pct": _pct_change(history["fed_assets"], 1),
        "fed_assets_d5obs_pct": _pct_change(history["fed_assets"], 5),
        "rrp_d1_pct": _pct_change(history["rrp"], 1),
        "rrp_d5obs_pct": _pct_change(history["rrp"], 5),
        "tga_d1_pct": _pct_change(history["tga"], 1),
        "tga_d5obs_pct": _pct_change(history["tga"], 5),
        "boj_call_rate_d1_bp": None if _delta(history["boj_call_rate"], 1) is None else _delta(history["boj_call_rate"], 1) * 100.0,
    }

    required_factor_names = tuple(name for name in factors if not name.startswith("boj_"))
    insufficient = tuple(name for name in required_factor_names if factors[name] is None)
    snapshot = MacroFactorSnapshot(
        as_of=cut.isoformat(),
        factors=factors,
        missing_required_series=missing_required,
        insufficient_history_factors=insufficient,
        complete=not missing_required and not insufficient,
    )
    snapshot.validate()
    return snapshot
