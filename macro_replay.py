"""Leakage-safe research labels for macro factor validation.

Feature generation and future outcomes are intentionally separate.  This file
may look forward only while constructing labels after an ``as_of`` cut; no
future price is exposed to ``macro_factors``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Iterable

from macro_factors import MacroFactorSnapshot


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _time(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


@dataclass(frozen=True)
class PricePoint:
    timestamp: str
    price: float

    def validate(self) -> None:
        _time(self.timestamp)
        if not math.isfinite(float(self.price)) or self.price <= 0:
            raise ValueError("price must be finite and positive")


@dataclass(frozen=True)
class ForwardOutcome:
    horizon_seconds: int
    anchor_time: str
    anchor_price: float
    end_time: str
    end_price: float
    return_pct: float
    mfe_pct: float
    mae_pct: float

    def validate(self) -> None:
        if self.horizon_seconds <= 0:
            raise ValueError("horizon must be positive")
        if _time(self.end_time) <= _time(self.anchor_time):
            raise ValueError("outcome must end after anchor")
        for value in (self.anchor_price, self.end_price, self.return_pct, self.mfe_pct, self.mae_pct):
            if not math.isfinite(float(value)):
                raise ValueError("outcome values must be finite")
        if self.anchor_price <= 0 or self.end_price <= 0:
            raise ValueError("outcome prices must be positive")
        if self.mfe_pct < self.mae_pct:
            raise ValueError("MFE cannot be below MAE")


@dataclass(frozen=True)
class ReplayRow:
    as_of: str
    factors: dict[str, float | None]
    factor_complete: bool
    outcomes: dict[str, ForwardOutcome]


def normalize_prices(points: Iterable[PricePoint]) -> tuple[PricePoint, ...]:
    rows = tuple(points)
    prior: datetime | None = None
    for row in rows:
        row.validate()
        ts = _time(row.timestamp)
        if prior is not None and ts <= prior:
            raise ValueError("price points must be strictly chronological")
        prior = ts
    return rows


def build_forward_outcome(
    prices: Iterable[PricePoint],
    *,
    as_of: datetime,
    horizon: timedelta,
) -> ForwardOutcome | None:
    """Label one cut using a known-at-cut anchor and future evaluation prices."""
    if horizon.total_seconds() <= 0:
        raise ValueError("horizon must be positive")
    rows = normalize_prices(prices)
    cut = _utc(as_of)
    target = cut + horizon

    # Anchor can only be a price observed at or before the feature cut.
    anchors = [row for row in rows if _time(row.timestamp) <= cut]
    if not anchors:
        return None
    anchor = anchors[-1]

    # Endpoint is first observation at/after the requested horizon. This is label
    # construction only and therefore intentionally uses future information.
    endings = [row for row in rows if _time(row.timestamp) >= target]
    if not endings:
        return None
    end = endings[0]

    path = [row for row in rows if _time(anchor.timestamp) < _time(row.timestamp) <= _time(end.timestamp)]
    if not path:
        return None

    anchor_price = float(anchor.price)
    returns = [(float(row.price) / anchor_price - 1.0) * 100.0 for row in path]
    outcome = ForwardOutcome(
        horizon_seconds=int(horizon.total_seconds()),
        anchor_time=anchor.timestamp,
        anchor_price=anchor_price,
        end_time=end.timestamp,
        end_price=float(end.price),
        return_pct=(float(end.price) / anchor_price - 1.0) * 100.0,
        mfe_pct=max(returns),
        mae_pct=min(returns),
    )
    outcome.validate()
    return outcome


def build_replay_row(
    snapshot: MacroFactorSnapshot,
    prices: Iterable[PricePoint],
    *,
    horizons: tuple[timedelta, ...] = (timedelta(days=1), timedelta(days=3), timedelta(days=7)),
) -> ReplayRow:
    snapshot.validate()
    rows = tuple(prices)
    outcomes: dict[str, ForwardOutcome] = {}
    for horizon in horizons:
        outcome = build_forward_outcome(rows, as_of=_time(snapshot.as_of), horizon=horizon)
        if outcome is not None:
            outcomes[f"{int(horizon.total_seconds())}s"] = outcome
    return ReplayRow(
        as_of=snapshot.as_of,
        factors=dict(snapshot.factors),
        factor_complete=snapshot.complete,
        outcomes=outcomes,
    )
