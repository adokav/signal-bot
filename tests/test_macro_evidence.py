from datetime import datetime, timedelta, timezone

import pytest

from macro_evidence import build_evidence_report, non_overlapping_indices
from macro_replay import ForwardOutcome, ReplayRow


def _row(day: int, factor: float, horizon_days: int, ret: float) -> ReplayRow:
    start = datetime(2020, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=day)
    end = start + timedelta(days=horizon_days)
    outcome = ForwardOutcome(
        horizon_seconds=horizon_days * 86400,
        anchor_time=start.isoformat(),
        anchor_price=100.0,
        end_time=end.isoformat(),
        end_price=100.0 * (1 + ret / 100.0),
        return_pct=ret,
        mfe_pct=max(ret, 1.0),
        mae_pct=min(ret, -1.0),
    )
    outcome.validate()
    return ReplayRow(
        as_of=start.isoformat(),
        factors={"x": factor},
        factor_complete=True,
        outcomes={f"{horizon_days * 86400}s": outcome},
    )


def test_non_overlapping_indices_do_not_double_count_7d_windows():
    rows = tuple(_row(day, float(day), 7, 1.0) for day in range(15))
    selected = non_overlapping_indices(rows, range(len(rows)), horizon_key="604800s")
    assert selected == (0, 7, 14)


def test_evidence_report_uses_train_only_thresholds_and_has_no_score():
    rows = []
    for day in range(900):
        factor = float(day % 100)
        # Deterministic synthetic relation for geometry testing only.
        ret = 2.0 if factor >= 75 else (-2.0 if factor <= 25 else 0.1)
        rows.append(_row(day, factor, 1, ret))

    report = build_evidence_report(
        rows,
        factors=("x",),
        horizon_keys=("86400s",),
        min_train_rows=365,
        test_rows=90,
    )
    assert len(report) == 1
    summary = report[0]
    assert summary.comparable_split_count > 0
    assert summary.median_high_minus_low_return_pct is not None
    assert summary.median_high_minus_low_return_pct > 0
    assert not hasattr(summary, "score")
    assert not hasattr(summary, "regime")
    assert not hasattr(summary, "position_size")


def test_evidence_report_rejects_short_history():
    rows = [_row(day, float(day), 1, 1.0) for day in range(20)]
    with pytest.raises(ValueError, match="no valid purged walk-forward splits"):
        build_evidence_report(rows, factors=("x",), horizon_keys=("86400s",), min_train_rows=30, test_rows=10)
