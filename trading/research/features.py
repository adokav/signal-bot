"""Normalized, point-in-time features (spec §6, §20).

Absolute thresholds ("volume > $50M", "ATR > 3%") do not transfer between
assets. These helpers express a value relative to the instrument's own
recent history or to a benchmark.

Every trailing statistic at index ``i`` uses **only** values strictly
before ``i`` as its reference window, so a spike cannot dilute its own
baseline and nothing from the future enters (AGENTS.md §1).
"""

from __future__ import annotations

import math
from typing import Protocol, Sequence

from trading.research.metrics import mean, sample_stdev


class DataAlignmentError(ValueError):
    """Two series cannot be combined because their timestamps disagree."""


class TimedClose(Protocol):
    close_time: int
    close: float


def _check_finite(values: Sequence[float]) -> None:
    for v in values:
        if not math.isfinite(v):
            raise ValueError("non-finite value in feature input")


def trailing_zscore(values: Sequence[float], *, window: int) -> list[float | None]:
    """``(x_i - mean(prev window)) / stdev(prev window)``; ``None`` if undefined."""

    if window < 2:
        raise ValueError("window must be >= 2")
    _check_finite(values)
    out: list[float | None] = []
    for i, x in enumerate(values):
        if i < window:
            out.append(None)
            continue
        ref = values[i - window:i]
        sd = sample_stdev(ref)
        out.append((x - mean(ref)) / sd if sd > 0 else None)
    return out


def trailing_percentile_rank(values: Sequence[float], *, window: int) -> list[float | None]:
    """Share of the previous ``window`` values below ``x_i`` (ties count half)."""

    if window < 1:
        raise ValueError("window must be >= 1")
    _check_finite(values)
    out: list[float | None] = []
    for i, x in enumerate(values):
        if i < window:
            out.append(None)
            continue
        ref = values[i - window:i]
        below = sum(1 for v in ref if v < x)
        ties = sum(1 for v in ref if v == x)
        out.append((below + 0.5 * ties) / window)
    return out


def require_aligned(a: Sequence[TimedClose], b: Sequence[TimedClose]) -> None:
    """Refuse to combine series whose close timestamps differ (spec §4)."""

    if len(a) != len(b):
        raise DataAlignmentError("series lengths differ")
    for left, right in zip(a, b):
        if left.close_time != right.close_time:
            raise DataAlignmentError(
                f"timestamp mismatch {left.close_time} != {right.close_time}"
            )


def relative_strength(
    asset: Sequence[TimedClose],
    benchmark: Sequence[TimedClose],
    *,
    lookback: int,
) -> list[float | None]:
    """Asset log-return minus benchmark log-return over ``lookback`` bars, in %.

    +2% while the market is -4% is +6 points of relative strength; +3%
    while the market is +7% is -4 points, i.e. relative weakness.
    """

    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    require_aligned(asset, benchmark)
    out: list[float | None] = []
    for i in range(len(asset)):
        if i < lookback:
            out.append(None)
            continue
        a0, a1 = asset[i - lookback].close, asset[i].close
        b0, b1 = benchmark[i - lookback].close, benchmark[i].close
        if min(a0, a1, b0, b1) <= 0:
            raise ValueError("closes must be positive")
        out.append((math.log(a1 / a0) - math.log(b1 / b0)) * 100.0)
    return out
