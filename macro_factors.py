"""Deterministic macro factor transforms for research.

This module deliberately does *not* create a macro score, regime label, trade
permission or portfolio weight. It transforms archived point-in-time
observations into transparent features whose predictive value must be measured
out of sample before they may influence trading.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Iterable

from macro_data import MacroObservation

CORE_FACTOR_SERIES = (
    "us_2y", "us_10y", "us_30y", "us_10y_real", "fed_assets", "rrp", "tga",
    "broad_usd", "usdjpy",
)
# Japan public historical snapshots are unversioned. Keep them optional until a
# true first-seen archive or vintage-safe source exists; otherwise historical
# replay would be forced to treat unavailable retrospective values as required.
OPTIONAL_FACTOR_SERIES = ("jgb_2y", "jgb_10y", "jgb_30y", "boj_call_rate")
# Daily Japan rates may legitimately span a long weekend/holiday. Five calendar
# days tolerates that, but prevents a stopped collector from making an old JGB
# observation look current indefinitely.
JAPAN_MAX_OBSERVATION_AGE = timedelta(days=5)
JAPAN_CROSS_MARKET_ALIGNMENT = timedelta(days=3)


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


def visible_history(observations: Iterable[MacroObservation], series: str, *, as_of: datetime) -> tuple[MacroObservation, ...]:
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


def _fresh_history(history, *, as_of: datetime, max_age: timedelta):
    if not history:
        return ()
    latest = _time(history[-1].observation_time)
    age = _utc(as_of) - latest
    if age < timedelta(0) or age > max_age:
        return ()
    return history


def _latest(history):
    return _finite(history[-1].value) if history else None


def _delta(history, lag=1):
    if lag < 1:
        raise ValueError("lag must be positive")
    if len(history) <= lag:
        return None
    return _finite(history[-1].value) - _finite(history[-1-lag].value)


def _pct_change(history, lag=1):
    if lag < 1:
        raise ValueError("lag must be positive")
    if len(history) <= lag:
        return None
    current, previous = _finite(history[-1].value), _finite(history[-1-lag].value)
    if previous == 0:
        return None
    return (current / previous - 1.0) * 100.0


def _bp_delta(history, lag=1):
    value = _delta(history, lag)
    return None if value is None else value * 100.0


def _same_observation_day(*histories) -> bool:
    if any(not history for history in histories):
        return False
    days = {_time(history[-1].observation_time).date() for history in histories}
    return len(days) == 1


def _aligned_latest(left, right, tolerance: timedelta) -> bool:
    if not left or not right:
        return False
    delta = abs(_time(left[-1].observation_time) - _time(right[-1].observation_time))
    return delta <= tolerance


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
            raise ValueError("complete factor snapshot cannot contain required data gaps")


def build_factor_snapshot(observations: Iterable[MacroObservation], *, as_of: datetime) -> MacroFactorSnapshot:
    cut = _utc(as_of)
    rows = tuple(observations)
    history = {name: visible_history(rows, name, as_of=cut) for name in CORE_FACTOR_SERIES + OPTIONAL_FACTOR_SERIES}
    # Optional Japan factors must also be fresh. Otherwise one successful fetch
    # could keep an old JGB/BOJ level alive forever against newer US inputs.
    for name in OPTIONAL_FACTOR_SERIES:
        history[name] = _fresh_history(history[name], as_of=cut, max_age=JAPAN_MAX_OBSERVATION_AGE)

    missing_required = tuple(name for name in CORE_FACTOR_SERIES if not history[name])
    us2, us10, us30, real10 = (_latest(history[x]) for x in ("us_2y", "us_10y", "us_30y", "us_10y_real"))
    jgb2, jgb10, jgb30 = (_latest(history[x]) for x in ("jgb_2y", "jgb_10y", "jgb_30y"))
    jgb_curve_aligned = _same_observation_day(history["jgb_2y"], history["jgb_10y"], history["jgb_30y"])
    us_jp_aligned = _aligned_latest(history["us_10y"], history["jgb_10y"], JAPAN_CROSS_MARKET_ALIGNMENT)

    factors: dict[str, float | None] = {
        "us_2y_d1_bp": _bp_delta(history["us_2y"], 1),
        "us_10y_d1_bp": _bp_delta(history["us_10y"], 1),
        "us_30y_d1_bp": _bp_delta(history["us_30y"], 1),
        "us_10y_real_d1_bp": _bp_delta(history["us_10y_real"], 1),
        "us_2y_d5obs_bp": _bp_delta(history["us_2y"], 5),
        "us_10y_d5obs_bp": _bp_delta(history["us_10y"], 5),
        "us_30y_d5obs_bp": _bp_delta(history["us_30y"], 5),
        "curve_2s10s_bp": None if us2 is None or us10 is None else (us10-us2)*100.0,
        "curve_10s30s_bp": None if us10 is None or us30 is None else (us30-us10)*100.0,
        "real_10y_level_pct": real10,
        "fed_assets_d1_pct": _pct_change(history["fed_assets"], 1),
        "fed_assets_d5obs_pct": _pct_change(history["fed_assets"], 5),
        "rrp_d1_pct": _pct_change(history["rrp"], 1),
        "rrp_d5obs_pct": _pct_change(history["rrp"], 5),
        "tga_d1_pct": _pct_change(history["tga"], 1),
        "tga_d5obs_pct": _pct_change(history["tga"], 5),
        "broad_usd_d1_pct": _pct_change(history["broad_usd"], 1),
        "broad_usd_d5obs_pct": _pct_change(history["broad_usd"], 5),
        "usdjpy_d1_pct": _pct_change(history["usdjpy"], 1),
        "usdjpy_d5obs_pct": _pct_change(history["usdjpy"], 5),
        # Optional Japan transforms. They remain None when missing, stale or
        # misaligned; no synthetic neutral value is inserted.
        "jgb_2y_d1_bp": _bp_delta(history["jgb_2y"], 1),
        "jgb_10y_d1_bp": _bp_delta(history["jgb_10y"], 1),
        "jgb_30y_d1_bp": _bp_delta(history["jgb_30y"], 1),
        "jgb_2y_d5obs_bp": _bp_delta(history["jgb_2y"], 5),
        "jgb_10y_d5obs_bp": _bp_delta(history["jgb_10y"], 5),
        "jgb_30y_d5obs_bp": _bp_delta(history["jgb_30y"], 5),
        "jgb_curve_2s10s_bp": None if not jgb_curve_aligned else (jgb10-jgb2)*100.0,
        "jgb_curve_10s30s_bp": None if not jgb_curve_aligned else (jgb30-jgb10)*100.0,
        "us_jp_10y_spread_bp": None if not us_jp_aligned else (us10-jgb10)*100.0,
        "boj_call_rate_d1_bp": _bp_delta(history["boj_call_rate"], 1),
        "boj_call_rate_d5obs_bp": _bp_delta(history["boj_call_rate"], 5),
    }
    required_factor_names = tuple(name for name in factors if not name.startswith("jgb_") and not name.startswith("boj_") and name != "us_jp_10y_spread_bp")
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
