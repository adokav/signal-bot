from __future__ import annotations

from acce_unified.radar_gate import liquid_long_gate, status_line, tactical_gate

NOW = 1_800_000_000


def _plan(**overrides):
    plan = {
        "entry_low": 100.0, "entry_high": 101.0, "technical_invalidation": 98.0,
        "hard_stop": 97.0, "target_1": 106.0, "target_2": 110.0,
    }
    plan.update(overrides)
    return plan


def test_fresh_valid_plan_is_watch_never_higher():
    decision = tactical_gate(
        {"symbol": "BTCUSDT", "decision_at": NOW - 60, "structure_4h": "BULLISH", "plan": _plan()},
        now=NOW, max_age_seconds=900,
    )
    assert decision.status == "WATCH"
    assert decision.no_trade and decision.can_authorize_trade is False
    by_key = {c.key: c.status.value for c in decision.checks}
    assert by_key["execution_healthy"] == "FAIL"
    assert by_key["oos_expectancy_positive"] == "UNKNOWN"


def test_bearish_no_plan_and_broken_geometry_are_rejected():
    bearish = tactical_gate(
        {"symbol": "ETHUSDT", "decision_at": NOW, "structure_4h": "BEARISH", "reasons": ["4H_STRUCTURE_BEARISH"]},
        now=NOW, max_age_seconds=900,
    )
    assert bearish.status == "REJECT"
    broken = tactical_gate(
        {"symbol": "BTCUSDT", "decision_at": NOW, "structure_4h": "BULLISH",
         "plan": _plan(hard_stop=99.0)},
        now=NOW, max_age_seconds=900,
    )
    assert broken.status == "REJECT"
    assert "stop teknik geçersizlik" in status_line(broken)


def test_unreadable_timestamp_is_not_treated_as_fresh():
    decision = tactical_gate(
        {"symbol": "BTCUSDT", "decision_at": None, "structure_4h": "BULLISH", "plan": _plan()},
        now=NOW, max_age_seconds=900,
    )
    assert decision.status == "WATCH"
    assert any(c.key == "data_fresh" and c.status.value == "UNKNOWN" for c in decision.checks)
    future = tactical_gate(
        {"symbol": "BTCUSDT", "decision_at": NOW + 600, "structure_4h": "BULLISH", "plan": _plan()},
        now=NOW, max_age_seconds=900,
    )
    assert future.status == "REJECT"


def test_liquid_long_missing_spread_and_neutral_regime_stay_unknown():
    decision = liquid_long_gate(
        {"symbol": "SOLUSDT", "metadata": {}}, market_regime="NEUTRAL",
        generated_at=NOW - 5, now=NOW, max_age_seconds=360,
    )
    assert decision.status == "WATCH"
    statuses = {c.key: c.status.value for c in decision.checks}
    assert statuses["liquid_enough"] == "UNKNOWN"
    assert statuses["regime_compatible"] == "UNKNOWN"
    assert statuses["stop_at_invalidation"] == "UNKNOWN"
