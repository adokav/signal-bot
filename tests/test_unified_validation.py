from __future__ import annotations

import bot

from acce_unified.validation import ValidationPolicy, evaluate_promotion, summarize_r_multiples


def test_r_metrics_include_profit_factor_expectancy_and_drawdown():
    metrics = summarize_r_multiples([1.5, -1.0, 2.0, -0.5, 1.0])
    assert metrics["trades"] == 5
    assert metrics["profit_factor"] == 3.0
    assert metrics["expectancy_r"] == 0.6
    assert metrics["max_drawdown_r"] == 1.0
    assert 0.0 <= metrics["win_rate_wilson_low"] <= metrics["win_rate"]


def test_promotion_uses_tail_as_out_of_sample():
    policy = ValidationPolicy(
        min_total_trades=10,
        min_oos_trades=4,
        oos_fraction=0.4,
        min_profit_factor=1.1,
        min_expectancy_r=0.05,
        max_drawdown_r=3.0,
    )
    good = [0.5, -0.2, 0.8, -0.3, 0.7, -0.2, 1.0, -0.2, 0.8, 0.6]
    report = evaluate_promotion(good, policy)
    assert report["passed"] is True
    assert report["decision"] == "PROMOTE_TO_ADVISORY"
    assert report["out_of_sample"]["trades"] == 4


def test_good_training_history_cannot_hide_bad_oos_tail():
    policy = ValidationPolicy(min_total_trades=10, min_oos_trades=4, oos_fraction=0.4)
    values = [1.0] * 6 + [-1.0, -1.0, -0.5, -0.5]
    report = evaluate_promotion(values, policy)
    assert report["passed"] is False
    assert report["decision"] == "KEEP_SHADOW"
    assert any("OOS" in reason for reason in report["reasons"])


class _FakeState:
    def __init__(self, trades):
        self._trades = trades

    def get_trades(self):
        return self._trades


def test_bot_promotion_sample_only_uses_radar_confluent_trades():
    base = {
        "result": "WIN",
        "symbol": "BTCUSDT",
        "group": "CORE",
        "direction": "LONG",
        "pnl_pct": 0.02,
        "closed_at": 10,
    }
    radar_trade = {
        **base,
        "unified_radar": {"mode": "SHADOW", "candidate": {"symbol": "BTCUSDT", "score": 75}},
    }
    plain_trade = {**base, "closed_at": 20}
    report = bot.unified_validation_report(
        _FakeState({"radar": radar_trade, "plain": plain_trade})
    )
    assert report["closed_trades_with_r"] == 2
    assert report["eligible_r_trades"] == 1
    assert report["skipped_without_radar_confluence"] == 1
