from datetime import datetime, timedelta, timezone

from macro_dataset import daily_replay_cuts
from macro_factors import MacroFactorSnapshot
from macro_replay import PriceBar, build_forward_outcome_from_bars, build_replay_row_from_bars


def test_daily_replay_cuts_are_end_of_utc_day_and_inclusive():
    cuts = daily_replay_cuts(start="2020-01-01", end="2020-01-02")
    assert [cut.isoformat() for cut in cuts] == [
        "2020-01-01T23:59:59+00:00",
        "2020-01-02T23:59:59+00:00",
    ]


def test_bar_outcome_uses_intrabar_high_low_for_mfe_mae():
    bars = (
        PriceBar("2020-01-01T23:00:00+00:00", 99, 101, 98, 100),
        PriceBar("2020-01-02T11:00:00+00:00", 100, 120, 80, 110),
        PriceBar("2020-01-02T23:00:00+00:00", 110, 112, 108, 105),
    )
    outcome = build_forward_outcome_from_bars(
        bars,
        as_of=datetime(2020, 1, 1, 23, 30, tzinfo=timezone.utc),
        horizon=timedelta(hours=23),
    )
    assert outcome is not None
    assert outcome.anchor_price == 100
    assert outcome.end_price == 105
    assert outcome.return_pct == 5.0
    assert outcome.mfe_pct == 20.0
    assert outcome.mae_pct == -20.0


def test_replay_row_from_bars_does_not_create_score_or_regime():
    snapshot = MacroFactorSnapshot(
        as_of="2020-01-01T23:30:00+00:00",
        factors={"x": 1.0},
        missing_required_series=(),
        insufficient_history_factors=(),
        complete=True,
    )
    bars = (
        PriceBar("2020-01-01T23:00:00+00:00", 99, 101, 98, 100),
        PriceBar("2020-01-02T23:30:00+00:00", 100, 111, 95, 110),
    )
    row = build_replay_row_from_bars(snapshot, bars, horizons=(timedelta(days=1),))
    assert row.factor_complete is True
    assert "86400s" in row.outcomes
    assert not hasattr(row, "score")
    assert not hasattr(row, "regime")
