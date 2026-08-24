from datetime import datetime, timedelta, timezone

from macro_data import MacroObservation
from macro_factors import build_factor_snapshot, visible_history


def _obs(series, day, value, available_day=None):
    available_day = available_day or day
    return MacroObservation(
        provider="test",
        series=series,
        observation_time=f"2026-08-{day:02d}T00:00:00+00:00",
        available_time=f"2026-08-{available_day:02d}T12:00:00+00:00",
        ingested_time=f"2026-08-{available_day:02d}T12:00:01+00:00",
        value=value,
    )


def _core_history():
    rows = []
    levels = {
        "us_2y": 4.0,
        "us_10y": 4.5,
        "us_30y": 5.0,
        "us_10y_real": 2.0,
        "fed_assets": 7_000_000.0,
        "rrp": 100.0,
        "tga": 800_000.0,
    }
    increments = {
        "us_2y": 0.01,
        "us_10y": 0.02,
        "us_30y": 0.03,
        "us_10y_real": 0.01,
        "fed_assets": 10_000.0,
        "rrp": -5.0,
        "tga": 20_000.0,
    }
    for series, start in levels.items():
        for i, day in enumerate(range(14, 20)):
            rows.append(_obs(series, day, start + increments[series] * i))
    return rows


def test_visible_history_excludes_future_available_revision():
    rows = [
        _obs("us_10y", 19, 4.5, 19),
        MacroObservation(
            provider="test",
            series="us_10y",
            observation_time="2026-08-19T00:00:00+00:00",
            available_time="2026-08-21T12:00:00+00:00",
            ingested_time="2026-08-21T12:00:01+00:00",
            value=9.9,
        ),
    ]
    history = visible_history(rows, "us_10y", as_of=datetime(2026, 8, 20, 18, tzinfo=timezone.utc))
    assert len(history) == 1
    assert history[-1].value == 4.5


def test_factor_snapshot_uses_basis_points_and_curve_geometry():
    snapshot = build_factor_snapshot(
        _core_history(),
        as_of=datetime(2026, 8, 20, 18, tzinfo=timezone.utc),
    )
    assert snapshot.complete is True
    assert round(snapshot.factors["us_10y_d1_bp"], 8) == 2.0
    assert round(snapshot.factors["us_30y_d5obs_bp"], 8) == 15.0
    # Latest levels: 2Y=4.05, 10Y=4.60, 30Y=5.15.
    assert round(snapshot.factors["curve_2s10s_bp"], 8) == 55.0
    assert round(snapshot.factors["curve_10s30s_bp"], 8) == 55.0


def test_factor_snapshot_does_not_pretend_insufficient_history_is_neutral():
    rows = [_obs(name, 19, value) for name, value in {
        "us_2y": 4.0,
        "us_10y": 4.5,
        "us_30y": 5.0,
        "us_10y_real": 2.0,
        "fed_assets": 7_000_000.0,
        "rrp": 100.0,
        "tga": 800_000.0,
    }.items()]
    snapshot = build_factor_snapshot(rows, as_of=datetime(2026, 8, 20, 18, tzinfo=timezone.utc))
    assert snapshot.complete is False
    assert "us_10y_d1_bp" in snapshot.insufficient_history_factors
    assert snapshot.factors["us_10y_d1_bp"] is None


def test_optional_boj_gap_does_not_block_core_factor_completeness():
    snapshot = build_factor_snapshot(
        _core_history(),
        as_of=datetime(2026, 8, 20, 18, tzinfo=timezone.utc),
    )
    assert snapshot.factors["boj_call_rate_d1_bp"] is None
    assert snapshot.complete is True


def test_factor_snapshot_has_no_score_or_regime_field():
    snapshot = build_factor_snapshot(
        _core_history(),
        as_of=datetime(2026, 8, 20, 18, tzinfo=timezone.utc),
    )
    assert not hasattr(snapshot, "score")
    assert not hasattr(snapshot, "regime")
    assert not hasattr(snapshot, "trade_permission")
