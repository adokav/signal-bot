"""Out-of-sample evidence summaries for macro factor research.

This layer still does not create a macro score or regime. It answers a narrower
question: when a factor is low/high relative to thresholds learned only on the
training sample, what happened to forward BTC outcomes out of sample?

For multi-day horizons, daily replay labels overlap heavily. Treating those rows
as independent would overstate the amount of evidence. Therefore test outcomes
are thinned chronologically so selected forward windows do not overlap before
split-level and aggregate summaries are computed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean, median
from typing import Iterable

from macro_replay import ReplayRow
from macro_validation import WalkForwardSplit, evaluate_factor_buckets, purged_walk_forward_splits


def _time(value: str):
    from datetime import datetime, timezone

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def non_overlapping_indices(
    rows: Iterable[ReplayRow],
    indices: Iterable[int],
    *,
    horizon_key: str,
) -> tuple[int, ...]:
    """Keep chronologically first test rows whose label windows do not overlap."""
    data = tuple(rows)
    selected: list[int] = []
    last_end = None
    for idx in sorted(indices, key=lambda i: _time(data[i].as_of)):
        outcome = data[idx].outcomes.get(horizon_key)
        if outcome is None:
            continue
        start = _time(data[idx].as_of)
        end = _time(outcome.end_time)
        if last_end is not None and start < last_end:
            continue
        selected.append(idx)
        last_end = end
    return tuple(selected)


@dataclass(frozen=True)
class BucketStats:
    n: int
    mean_return_pct: float
    median_return_pct: float
    hit_rate_pct: float
    mean_mfe_pct: float
    mean_mae_pct: float


def _stats(rows: list[ReplayRow], horizon_key: str) -> BucketStats | None:
    outcomes = [row.outcomes[horizon_key] for row in rows if horizon_key in row.outcomes]
    if not outcomes:
        return None
    returns = [float(item.return_pct) for item in outcomes]
    return BucketStats(
        n=len(outcomes),
        mean_return_pct=mean(returns),
        median_return_pct=median(returns),
        hit_rate_pct=100.0 * sum(value > 0 for value in returns) / len(returns),
        mean_mfe_pct=mean(float(item.mfe_pct) for item in outcomes),
        mean_mae_pct=mean(float(item.mae_pct) for item in outcomes),
    )


@dataclass(frozen=True)
class SplitFactorEvidence:
    factor: str
    horizon_key: str
    test_start: str
    train_q25: float
    train_q75: float
    selected_test_n: int
    low: BucketStats | None
    middle: BucketStats | None
    high: BucketStats | None
    high_minus_low_mean_return_pct: float | None
    high_minus_low_hit_rate_pct: float | None
    high_minus_low_mean_mae_pct: float | None

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_split_evidence(
    rows: Iterable[ReplayRow],
    split: WalkForwardSplit,
    *,
    factor: str,
    horizon_key: str,
) -> SplitFactorEvidence:
    """Evaluate one split using train-only thresholds and non-overlap test rows."""
    data = tuple(rows)
    thresholds = evaluate_factor_buckets(data, split, factor=factor, horizon_key=horizon_key)
    selected = non_overlapping_indices(data, split.test_indices, horizon_key=horizon_key)
    low_rows: list[ReplayRow] = []
    middle_rows: list[ReplayRow] = []
    high_rows: list[ReplayRow] = []
    for idx in selected:
        row = data[idx]
        value = row.factors.get(factor)
        if value is None:
            continue
        numeric = float(value)
        if numeric <= thresholds.train_q25:
            low_rows.append(row)
        elif numeric >= thresholds.train_q75:
            high_rows.append(row)
        else:
            middle_rows.append(row)

    low = _stats(low_rows, horizon_key)
    middle = _stats(middle_rows, horizon_key)
    high = _stats(high_rows, horizon_key)
    return SplitFactorEvidence(
        factor=factor,
        horizon_key=horizon_key,
        test_start=split.test_start,
        train_q25=thresholds.train_q25,
        train_q75=thresholds.train_q75,
        selected_test_n=len(selected),
        low=low,
        middle=middle,
        high=high,
        high_minus_low_mean_return_pct=None if low is None or high is None else high.mean_return_pct - low.mean_return_pct,
        high_minus_low_hit_rate_pct=None if low is None or high is None else high.hit_rate_pct - low.hit_rate_pct,
        high_minus_low_mean_mae_pct=None if low is None or high is None else high.mean_mae_pct - low.mean_mae_pct,
    )


@dataclass(frozen=True)
class FactorEvidenceSummary:
    factor: str
    horizon_key: str
    split_count: int
    comparable_split_count: int
    selected_test_n: int
    low_n: int
    high_n: int
    median_high_minus_low_return_pct: float | None
    mean_high_minus_low_return_pct: float | None
    return_effect_positive_split_pct: float | None
    median_high_minus_low_hit_rate_pct: float | None
    median_high_minus_low_mae_pct: float | None

    def to_dict(self) -> dict:
        return asdict(self)


def summarize_factor_evidence(evidence: Iterable[SplitFactorEvidence]) -> FactorEvidenceSummary:
    rows = tuple(evidence)
    if not rows:
        raise ValueError("factor evidence requires at least one split")
    factor = rows[0].factor
    horizon = rows[0].horizon_key
    if any(row.factor != factor or row.horizon_key != horizon for row in rows):
        raise ValueError("cannot aggregate mixed factor/horizon evidence")

    comparable = [row for row in rows if row.high_minus_low_mean_return_pct is not None]
    return_effects = [float(row.high_minus_low_mean_return_pct) for row in comparable]
    hit_effects = [float(row.high_minus_low_hit_rate_pct) for row in comparable if row.high_minus_low_hit_rate_pct is not None]
    mae_effects = [float(row.high_minus_low_mean_mae_pct) for row in comparable if row.high_minus_low_mean_mae_pct is not None]
    return FactorEvidenceSummary(
        factor=factor,
        horizon_key=horizon,
        split_count=len(rows),
        comparable_split_count=len(comparable),
        selected_test_n=sum(row.selected_test_n for row in rows),
        low_n=sum(row.low.n for row in rows if row.low is not None),
        high_n=sum(row.high.n for row in rows if row.high is not None),
        median_high_minus_low_return_pct=median(return_effects) if return_effects else None,
        mean_high_minus_low_return_pct=mean(return_effects) if return_effects else None,
        return_effect_positive_split_pct=(100.0 * sum(value > 0 for value in return_effects) / len(return_effects)) if return_effects else None,
        median_high_minus_low_hit_rate_pct=median(hit_effects) if hit_effects else None,
        median_high_minus_low_mae_pct=median(mae_effects) if mae_effects else None,
    )


def build_evidence_report(
    rows: Iterable[ReplayRow],
    *,
    factors: Iterable[str],
    horizon_keys: Iterable[str] = ("86400s", "259200s", "604800s"),
    min_train_rows: int = 365,
    test_rows: int = 90,
) -> tuple[FactorEvidenceSummary, ...]:
    """Build transparent OOS summaries; no ranking, score or trading action."""
    data = tuple(rows)
    splits = purged_walk_forward_splits(data, min_train_rows=min_train_rows, test_rows=test_rows)
    if not splits:
        raise ValueError("no valid purged walk-forward splits")
    report: list[FactorEvidenceSummary] = []
    for factor in factors:
        for horizon_key in horizon_keys:
            evidence: list[SplitFactorEvidence] = []
            for split in splits:
                try:
                    evidence.append(evaluate_split_evidence(data, split, factor=factor, horizon_key=horizon_key))
                except ValueError as exc:
                    # A split with fewer than four train values for a specific
                    # factor is insufficient evidence, not neutral evidence.
                    if "insufficient train factor history" in str(exc):
                        continue
                    raise
            if evidence:
                report.append(summarize_factor_evidence(evidence))
    return tuple(report)
