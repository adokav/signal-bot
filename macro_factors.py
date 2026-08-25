"""Deterministic macro factor transforms for research.

This module deliberately does *not* create a macro score, regime label, trade
permission or portfolio weight. It transforms archived point-in-time
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
    "us_2y", "us_10y", "us_30y", "us_10y_real", "fed_assets", "rrp", "tga",
    "broad_usd", "usdjpy",
)
OPTIONAL_FACTOR_SERIES = ("boj_call_rate",)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None: value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _utc(parsed)


def _finite(value: float) -> float:
    value = float(value)
    if not math.isfinite(value): raise ValueError("factor input must be finite")
    return value


def visible_history(observations: Iterable[MacroObservation], series: str, *, as_of: datetime) -> tuple[MacroObservation, ...]:
    cut = _utc(as_of)
    by_observation: dict[str, MacroObservation] = {}
    for obs in observations:
        obs.validate()
        if obs.series != series: continue
        if _time(obs.available_time) > cut or _time(obs.ingested_time) > cut: continue
        if _time(obs.observation_time) > cut: continue
        key = obs.observation_time
        prior = by_observation.get(key)
        if prior is None or _time(obs.available_time) > _time(prior.available_time): by_observation[key] = obs
    return tuple(sorted(by_observation.values(), key=lambda row: _time(row.observation_time)))


def _latest(history): return _finite(history[-1].value) if history else None

def _delta(history, lag=1):
    if lag < 1: raise ValueError("lag must be positive")
    if len(history) <= lag: return None
    return _finite(history[-1].value) - _finite(history[-1-lag].value)

def _pct_change(history, lag=1):
    if lag < 1: raise ValueError("lag must be positive")
    if len(history) <= lag: return None
    current, previous = _finite(history[-1].value), _finite(history[-1-lag].value)
    if previous == 0: return None
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
            if value is not None and not math.isfinite(float(value)): raise ValueError("macro factor must be finite")
        if self.complete and (self.missing_required_series or self.insufficient_history_factors): raise ValueError("complete factor snapshot cannot contain data gaps")


def build_factor_snapshot(observations: Iterable[MacroObservation], *, as_of: datetime) -> MacroFactorSnapshot:
    cut = _utc(as_of); rows = tuple(observations)
    history = {name: visible_history(rows, name, as_of=cut) for name in CORE_FACTOR_SERIES + OPTIONAL_FACTOR_SERIES}
    missing_required = tuple(name for name in CORE_FACTOR_SERIES if not history[name])
    us2, us10, us30, real10 = (_latest(history[x]) for x in ("us_2y", "us_10y", "us_30y", "us_10y_real"))
    factors: dict[str, float | None] = {
        "us_2y_d1_bp": None if _delta(history["us_2y"],1) is None else _delta(history["us_2y"],1)*100.0,
        "us_10y_d1_bp": None if _delta(history["us_10y"],1) is None else _delta(history["us_10y"],1)*100.0,
        "us_30y_d1_bp": None if _delta(history["us_30y"],1) is None else _delta(history["us_30y"],1)*100.0,
        "us_10y_real_d1_bp": None if _delta(history["us_10y_real"],1) is None else _delta(history["us_10y_real"],1)*100.0,
        "us_2y_d5obs_bp": None if _delta(history["us_2y"],5) is None else _delta(history["us_2y"],5)*100.0,
        "us_10y_d5obs_bp": None if _delta(history["us_10y"],5) is None else _delta(history["us_10y"],5)*100.0,
        "us_30y_d5obs_bp": None if _delta(history["us_30y"],5) is None else _delta(history["us_30y"],5)*100.0,
        "curve_2s10s_bp": None if us2 is None or us10 is None else (us10-us2)*100.0,
        "curve_10s30s_bp": None if us10 is None or us30 is None else (us30-us10)*100.0,
        "real_10y_level_pct": real10,
        "fed_assets_d1_pct": _pct_change(history["fed_assets"],1), "fed_assets_d5obs_pct": _pct_change(history["fed_assets"],5),
        "rrp_d1_pct": _pct_change(history["rrp"],1), "rrp_d5obs_pct": _pct_change(history["rrp"],5),
        "tga_d1_pct": _pct_change(history["tga"],1), "tga_d5obs_pct": _pct_change(history["tga"],5),
        # Direction is intentionally left uninterpreted here. Positive means the
        # broad USD index / USDJPY rose; evidence decides whether that matters.
        "broad_usd_d1_pct": _pct_change(history["broad_usd"],1),
        "broad_usd_d5obs_pct": _pct_change(history["broad_usd"],5),
        "usdjpy_d1_pct": _pct_change(history["usdjpy"],1),
        "usdjpy_d5obs_pct": _pct_change(history["usdjpy"],5),
        "boj_call_rate_d1_bp": None if _delta(history["boj_call_rate"],1) is None else _delta(history["boj_call_rate"],1)*100.0,
    }
    required_factor_names = tuple(name for name in factors if not name.startswith("boj_"))
    insufficient = tuple(name for name in required_factor_names if factors[name] is None)
    snapshot = MacroFactorSnapshot(as_of=cut.isoformat(), factors=factors, missing_required_series=missing_required, insufficient_history_factors=insufficient, complete=not missing_required and not insufficient)
    snapshot.validate(); return snapshot
