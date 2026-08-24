from datetime import datetime, timedelta, timezone

from macro_factors import MacroFactorSnapshot
from macro_replay import ForwardOutcome, ReplayRow
from macro_validation import evaluate_factor_buckets, purged_walk_forward_splits


def _row(day: int, factor: float, end_day: int, ret: float) -> ReplayRow:
    as_of = f"2026-08-{day:02d}T12:00:00+00:00"
    outcome = ForwardOutcome(
        horizon_seconds=86400,
        anchor_time=as_of,
        anchor_price=100.0,
        end_time=f"2026-08-{end_day:02d}T12:00:00+00:00",
        end_price=100.0 * (1 + ret / 100.0),
        return_pct=ret,
        mfe_pct=max(ret, 1.0),
        mae_pct=min(ret, -1.0),
    )
    outcome.validate()
    return ReplayRow(as_of=as_of, factors={"x": factor}, factor_complete=True, outcomes={"86400s": outcome})


def test_walk_forward_purges_training_labels_overlapping_test_start():
    rows = [
        _row(1, 1, 2, 1),
        _row(2, 2, 3, 1),
        _row(3, 3, 6, 1),  # overlaps first test start on day 5 -> must purge
        _row(4, 4, 5, 1),  # ends exactly at test start -> must purge
        _row(5, 5, 6, 1),
        _row(6, 6, 7, 1),
    ]
    splits = purged_walk_forward_splits(rows, min_train_rows=2, test_rows=2)
    assert len(splits) == 1
    assert splits[0].test_indices == (4, 5)
    assert splits[0].train_indices == (0, 1)


def test_bucket_thresholds_are_learned_from_train_only():
    rows = [
        _row(1, 1, 2, -1),
        _row(2, 2, 3, -1),
        _row(3, 3, 4, 1),
        _row(4, 4, 5, 1),
        _row(5, 1000, 6, 10),
        _row(6, -1000, 7, -10),
    ]
    split = purged_walk_forward_splits(rows, min_train_rows=4, test_rows=2)[0]
    result = evaluate_factor_buckets(rows, split, factor="x", horizon_key="86400s")
    assert result.train_q25 == 1.75
    assert result.train_q75 == 3.25
    assert result.high is not None and result.high.mean_return_pct == 10
    assert result.low is not None and result.low.mean_return_pct == -10


def test_no_score_or_model_is_created_by_validation_layer():
    rows = [_row(day, float(day), day + 1, float(day % 2)) for day in range(1, 7)]
    split = purged_walk_forward_splits(rows, min_train_rows=4, test_rows=2)[0]
    result = evaluate_factor_buckets(rows, split, factor="x", horizon_key="86400s")
    assert not hasattr(result, "score")
    assert not hasattr(result, "regime")
    assert not hasattr(result, "position_size")
