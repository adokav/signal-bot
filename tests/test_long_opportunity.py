from __future__ import annotations

import pytest

from acce_unified.long_opportunity import (
    LongPathLabel,
    LongSetup,
    PathOutcome,
    classify_setup,
    evaluate_long_funnel,
)


def _ready(**overrides):
    row = {
        "status": "READY",
        "ema20_distance_pct": 1.0,
        "ema20_slope_pct": 0.2,
        "change_1h_pct": 0.4,
        "change_4h_pct": 2.0,
        "range_position_pct": 65.0,
        "volume_ratio": 1.2,
        "drawdown_from_high_pct": -2.0,
    }
    row.update(overrides)
    return row


def test_trend_pullback_is_distinct_from_breakout():
    assert classify_setup(_ready()) is LongSetup.TREND_PULLBACK
    assert classify_setup(_ready(
        ema20_distance_pct=4.0,
        range_position_pct=91.0,
        volume_ratio=2.0,
        change_1h_pct=1.2,
    )) is LongSetup.BREAKOUT_CONFIRMATION


def test_missing_data_never_becomes_a_setup():
    assert classify_setup({"status": "PROVIDER_UNAVAILABLE"}) is LongSetup.NO_VALID_SETUP


def test_hard_veto_is_separate_from_soft_rejection():
    decision = evaluate_long_funnel(
        symbol="SOLUSDT",
        metrics=_ready(),
        blockers=["EXTREME_SPREAD", "RSI_OVERHEATED"],
        current_rule_score=82,
    )
    assert decision.hard_vetoes == ("EXTREME_SPREAD",)
    assert decision.soft_rejections == ("RSI_OVERHEATED",)
    assert decision.accepted_for_shadow is False
    assert decision.can_authorize_trade is False


def test_zero_candidate_behavior_is_explicit():
    decision = evaluate_long_funnel(
        symbol="BTCUSDT",
        metrics=_ready(
            ema20_distance_pct=5.0,
            ema20_slope_pct=-0.1,
            change_1h_pct=-0.2,
            change_4h_pct=-1.0,
            range_position_pct=40.0,
            volume_ratio=0.7,
        ),
    )
    assert decision.setup is LongSetup.NO_VALID_SETUP
    assert "NO_VALID_SETUP" in decision.soft_rejections
    assert decision.accepted_for_shadow is False


def test_path_label_validates_trade_horizon():
    label = LongPathLabel(
        symbol="SOLUSDT",
        setup=LongSetup.TREND_PULLBACK,
        decision_at=1_700_000_000,
        horizon_seconds=14_400,
        outcome=PathOutcome.TP_BEFORE_SL,
        entry_price=100.0,
        stop_price=97.0,
        target_price=106.0,
        mfe_pct=6.2,
        mae_pct=-1.1,
        net_return_pct=5.7,
        holding_seconds=7_200,
    )
    assert label.outcome is PathOutcome.TP_BEFORE_SL

    with pytest.raises(ValueError):
        LongPathLabel(
            symbol="SOLUSDT",
            setup=LongSetup.TREND_PULLBACK,
            decision_at=1_700_000_000,
            horizon_seconds=3_600,
            outcome=PathOutcome.TIMEOUT,
            entry_price=100.0,
            stop_price=97.0,
            target_price=106.0,
            mfe_pct=1.0,
            mae_pct=-1.0,
            net_return_pct=0.0,
            holding_seconds=3_601,
        )
