"""Descriptive trend/volatility regime labels for performance attribution (spec §7).

These labels exist so a backtest can answer "where did the P&L come from?"
— they are **not** a regime model and carry no trading permission
(AGENTS.md §4, §6). Two axes, both computed from closes visible at the
decision bar only:

- trend: close vs its trailing ``trend_window`` simple average, with a
  ±``band_pct`` neutral band → ``UP`` / ``DOWN`` / ``RANGE``;
- volatility: current ``vol_window`` realized volatility ranked against the
  previous ``vol_history`` daily readings → ``LOW`` (≤20th pct),
  ``HIGH`` (≥80th pct) or ``NORMAL``.

Thresholds are round robust zones chosen before looking at any result.
The richer regimes listed in the spec (dominance rotation, short squeeze,
liquidation cascade, post-news) need data this system does not ingest yet
and are deliberately not approximated from price alone.
"""

from __future__ import annotations

import math
from typing import Sequence

from trading.research.metrics import sample_stdev


UNKNOWN = "UNKNOWN"


def _realized_vol(closes: Sequence[float], end: int, window: int) -> float | None:
    if end - window < 0:
        return None
    rets = [math.log(closes[k] / closes[k - 1]) for k in range(end - window + 1, end + 1)]
    return sample_stdev(rets) * math.sqrt(365.0) * 100.0


def trend_state(closes: Sequence[float], *, trend_window: int = 200, band_pct: float = 2.0) -> str:
    if len(closes) < trend_window:
        return UNKNOWN
    sma = sum(closes[-trend_window:]) / trend_window
    distance = (closes[-1] / sma - 1.0) * 100.0
    if distance > band_pct:
        return "UP"
    if distance < -band_pct:
        return "DOWN"
    return "RANGE"


def volatility_state(
    closes: Sequence[float],
    *,
    vol_window: int = 30,
    vol_history: int = 365,
    low_pct: float = 0.2,
    high_pct: float = 0.8,
) -> str:
    last = len(closes) - 1
    if last - vol_history - vol_window < 1:
        return UNKNOWN
    current = _realized_vol(closes, last, vol_window)
    history = [
        v for v in (
            _realized_vol(closes, end, vol_window)
            for end in range(last - vol_history, last)
        )
        if v is not None
    ]
    if current is None or not history:
        return UNKNOWN
    rank = sum(1 for v in history if v < current) / len(history)
    if rank >= high_pct:
        return "HIGH_VOL"
    if rank <= low_pct:
        return "LOW_VOL"
    return "NORMAL_VOL"


def trend_vol_regime(closes: Sequence[float]) -> str:
    """Label for the last bar of ``closes`` (all of which must be closed)."""

    for c in closes:
        if not math.isfinite(c) or c <= 0:
            raise ValueError("closes must be finite and positive")
    trend = trend_state(closes)
    vol = volatility_state(closes)
    if UNKNOWN in (trend, vol):
        return UNKNOWN
    return f"{trend}_{vol}"
