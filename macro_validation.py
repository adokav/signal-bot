"""Purged walk-forward validation helpers for macro research.

No model, score or trading decision is produced here. The only purpose is to
prevent overlapping future labels from leaking across train/test boundaries and
to summarize out-of-sample factor buckets using thresholds learned on train.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean, median
from typing import Iterable

from macro_replay import ReplayRow


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _max_outcome_end(row: ReplayRow) -> datetime:
    if not row.outcomes:
        return _utc(row.as_of)
    return max(_utc(outcome.end_time) for outcome in row.outcomes.values())


@dataclass(frozen=True)
class WalkForwardSplit:
    train_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    test_start: str


def purged_walk_forward_splits(
    rows: Iterable[ReplayRow],
    *,
    min_train_rows: int,
    test_rows: int,
) -> tuple[WalkForwardSplit, ...]:
    """Expanding walk-forward splits with label-overlap purge.

    A training row is excluded if any of its future outcomes ends at or after
    the first feature timestamp of the test block.
    """
    if min_train_rows < 1 or test_rows < 1:
        raise ValueError("split sizes must be positive")
    data = tuple(rows)
    for left, right in zip(data, data[1:]):
        if _utc(left.as_of) >= _utc(right.as_of):
            raise ValueError("replay rows must be strictly chronological")
    splits: list[WalkForwardSplit] = []
    start = min_train_rows
    while start < len(data):
        stop = min(len(data), start + test_rows)
        test_idx = tuple(range(start, stop))
        test_start_dt = _utc(data[start].as_of)
        train_idx = tuple(
            idx for idx in range(start)
            if _max_outcome_end(data[idx]) < test_start_dt
        )
        if len(train_idx) >= min_train_rows:
            splits.append(WalkForwardSplit(train_idx, test_idx, data[start].as_of))
        start = stop
    return tuple(splits)


def _quantile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("quantile requires data")
    if not 0 <= q <= 1:
        raise ValueError("q must be in [0,1]")
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * q
    low = int(position)
    high = min(low + 1, len(values) - 1)
    fraction = position - low
    return values[low] * (1 - fraction) + values[high] * fraction


@dataclass(frozen=True)
class OutcomeStats:
    n: int
    mean_return_pct: float
    median_return_pct: float
    hit_rate_pct: float
    mean_mfe_pct: float
    mean_mae_pct: float


def _stats(rows: list[ReplayRow], horizon_key: str) -> OutcomeStats | None:
    outcomes = [row.outcomes[horizon_key] for row in rows if horizon_key in row.outcomes]
    if not outcomes:
        return None
    returns = [item.return_pct for item in outcomes]
    return OutcomeStats(
        n=len(outcomes),
        mean_return_pct=mean(returns),
        median_return_pct=median(returns),
        hit_rate_pct=100.0 * sum(value > 0 for value in returns) / len(returns),
        mean_mfe_pct=mean(item.mfe_pct for item in outcomes),
        mean_mae_pct=mean(item.mae_pct for item in outcomes),
    )


@dataclass(frozen=True)
class FactorBucketEvaluation:
    factor: str
    horizon_key: str
    train_q25: float
    train_q75: float
    low: OutcomeStats | None
    middle: OutcomeStats | None
    high: OutcomeStats | None


def evaluate_factor_buckets(
    rows: Iterable[ReplayRow],
    split: WalkForwardSplit,
    *,
    factor: str,
    horizon_key: str,
) -> FactorBucketEvaluation:
    """Evaluate test outcomes using factor thresholds learned only on train."""
    data = tuple(rows)
    train_values = [
        float(data[idx].factors[factor])
        for idx in split.train_indices
        if data[idx].factors.get(factor) is not None
    ]
    if len(train_values) < 4:
        raise ValueError("insufficient train factor history")
    q25 = _quantile(train_values, 0.25)
    q75 = _quantile(train_values, 0.75)

    low_rows: list[ReplayRow] = []
    mid_rows: list[ReplayRow] = []
    high_rows: list[ReplayRow] = []
    for idx in split.test_indices:
        row = data[idx]
        value = row.factors.get(factor)
        if value is None:
            continue
        numeric = float(value)
        if numeric <= q25:
            low_rows.append(row)
        elif numeric >= q75:
            high_rows.append(row)
        else:
            mid_rows.append(row)

    return FactorBucketEvaluation(
        factor=factor,
        horizon_key=horizon_key,
        train_q25=q25,
        train_q75=q75,
        low=_stats(low_rows, horizon_key),
        middle=_stats(mid_rows, horizon_key),
        high=_stats(high_rows, horizon_key),
    )
