from datetime import datetime, timedelta, timezone

import pytest

from macro_factors import MacroFactorSnapshot
from macro_replay import PricePoint, build_forward_outcome, build_replay_row


def _prices():
    return [
        PricePoint("2026-08-20T00:00:00+00:00", 100.0),
        PricePoint("2026-08-20T12:00:00+00:00", 102.0),
        PricePoint("2026-08-21T00:00:00+00:00", 98.0),
        PricePoint("2026-08-21T12:00:00+00:00", 105.0),
        PricePoint("2026-08-22T12:00:00+00:00", 110.0),
        PricePoint("2026-08-23T12:00:00+00:00", 95.0),
        PricePoint("2026-08-27T12:00:00+00:00", 120.0),
    ]


def test_forward_outcome_anchor_never_uses_future_price():
    outcome = build_forward_outcome(
        _prices(),
        as_of=datetime(2026, 8, 20, 18, tzinfo=timezone.utc),
        horizon=timedelta(days=1),
    )
    assert outcome is not None
    assert outcome.anchor_time == "2026-08-20T12:00:00+00:00"
    assert outcome.anchor_price == 102.0
    assert outcome.end_time == "2026-08-22T12:00:00+00:00"


def test_forward_outcome_computes_path_mfe_and_mae_from_anchor():
    outcome = build_forward_outcome(
        _prices(),
        as_of=datetime(2026, 8, 20, 18, tzinfo=timezone.utc),
        horizon=timedelta(days=1),
    )
    assert outcome is not None
    assert round(outcome.return_pct, 6) == round((110 / 102 - 1) * 100, 6)
    assert round(outcome.mfe_pct, 6) == round((110 / 102 - 1) * 100, 6)
    assert round(outcome.mae_pct, 6) == round((98 / 102 - 1) * 100, 6)


def test_forward_outcome_returns_none_without_future_horizon_data():
    assert build_forward_outcome(
        _prices()[:3],
        as_of=datetime(2026, 8, 20, 18, tzinfo=timezone.utc),
        horizon=timedelta(days=7),
    ) is None


def test_price_points_must_be_strictly_chronological():
    bad = [
        PricePoint("2026-08-20T12:00:00+00:00", 100.0),
        PricePoint("2026-08-20T11:00:00+00:00", 101.0),
    ]
    with pytest.raises(ValueError):
        build_forward_outcome(
            bad,
            as_of=datetime(2026, 8, 20, 18, tzinfo=timezone.utc),
            horizon=timedelta(days=1),
        )


def test_replay_row_keeps_features_and_labels_separate():
    snapshot = MacroFactorSnapshot(
        as_of="2026-08-20T18:00:00+00:00",
        factors={"us_10y_d1_bp": -5.0},
        missing_required_series=(),
        insufficient_history_factors=(),
        complete=True,
    )
    row = build_replay_row(snapshot, _prices(), horizons=(timedelta(days=1),))
    assert row.factor_complete is True
    assert row.factors == {"us_10y_d1_bp": -5.0}
    assert "86400s" in row.outcomes
    assert "return_pct" not in row.factors
