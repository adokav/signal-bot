from __future__ import annotations

from dataclasses import replace

import pytest

from acce_unified.tactical_long import (
    Candle,
    DataQualityError,
    PlanState,
    StructureState,
    Swing,
    SwingKind,
    TacticalSetup,
    TradePlan,
    atr,
    classify_structure,
    cluster_price_zones,
    confirmed_swings,
    visible_closed_candles,
)


def _candle(index: int, *, high: float, low: float, close: float | None = None) -> Candle:
    start = 1_700_000_000 + index * 900
    return Candle(
        open_time=start,
        close_time=start + 900,
        available_at=start + 901,
        ingested_at=start + 901,
        open=(high + low) / 2,
        high=high,
        low=low,
        close=close if close is not None else (high + low) / 2,
        volume=100 + index,
    )


@pytest.mark.parametrize("malformed", [float("nan"), float("inf"), 1.5, "901", True])
def test_candle_rejects_malformed_ingestion_vintage(malformed):
    candle = _candle(0, high=101.0, low=99.0)

    with pytest.raises(DataQualityError, match="positive finite integer"):
        replace(candle, ingested_at=malformed)


def test_open_or_future_candle_is_rejected_instead_of_silently_dropped():
    rows = [_candle(0, high=101, low=99), _candle(1, high=102, low=100)]
    with pytest.raises(DataQualityError, match="future or open"):
        visible_closed_candles(rows, decision_at=rows[-1].close_time)


def test_stale_data_fails_closed():
    row = _candle(0, high=101, low=99)
    with pytest.raises(DataQualityError, match="stale"):
        visible_closed_candles(
            [row], decision_at=row.close_time + 10_000, max_staleness_seconds=1_000
        )


def test_atr_uses_completed_ranges_and_requires_history():
    rows = [_candle(i, high=100 + i + 1, low=99 + i) for i in range(16)]
    assert atr(rows, period=14) > 0
    with pytest.raises(DataQualityError):
        atr(rows[:10], period=14)


def test_swings_are_confirmed_only_after_right_hand_candles_exist():
    rows = [
        _candle(0, high=100, low=98),
        _candle(1, high=103, low=99),
        _candle(2, high=101, low=97),
        _candle(3, high=104, low=100),
        _candle(4, high=102, low=96),
    ]
    swings = confirmed_swings(rows, left=1, right=1, minimum_move=1.0)
    assert swings
    for swing in swings:
        assert swing.confirmed_at > swing.event_time
        assert swing.available_at >= swing.confirmed_at
    # The final candle can never be a confirmed swing because no right candle exists.
    assert all(item.index < len(rows) - 1 for item in swings)


def test_noise_filter_drops_alternating_move_below_minimum_distance():
    rows = [
        _candle(0, high=100.0, low=99.0),
        _candle(1, high=101.0, low=99.5),
        _candle(2, high=100.2, low=99.7),
        _candle(3, high=101.2, low=99.6),
        _candle(4, high=100.4, low=99.8),
    ]
    swings = confirmed_swings(rows, left=1, right=1, minimum_move=2.0)
    assert len(swings) <= 1


def test_structure_classification_requires_two_highs_and_two_lows():
    base = 1_700_000_000
    bullish = [
        Swing(SwingKind.LOW, 1, 100, base, base + 1, base + 1),
        Swing(SwingKind.HIGH, 2, 110, base + 2, base + 3, base + 3),
        Swing(SwingKind.LOW, 3, 104, base + 4, base + 5, base + 5),
        Swing(SwingKind.HIGH, 4, 116, base + 6, base + 7, base + 7),
    ]
    assert classify_structure(bullish, equality_tolerance=0.5) is StructureState.BULLISH
    assert classify_structure(bullish[:3], equality_tolerance=0.5) is StructureState.INSUFFICIENT_DATA


def test_zone_clustering_keeps_touch_count_and_time_bounds():
    base = 1_700_000_000
    swings = [
        Swing(SwingKind.LOW, 1, 100.0, base, base + 1, base + 1),
        Swing(SwingKind.LOW, 3, 100.4, base + 100, base + 101, base + 101),
        Swing(SwingKind.HIGH, 5, 110.0, base + 200, base + 201, base + 201),
    ]
    zones = cluster_price_zones(swings, tolerance=0.5)
    assert len(zones) == 2
    first = zones[0]
    assert first.touches == 2
    assert first.low == 100.0
    assert first.high == 100.4
    assert first.first_touch_at == base
    assert first.last_touch_at == base + 100


def _valid_plan(**overrides) -> TradePlan:
    values = dict(
        symbol="BTCUSDT",
        setup=TacticalSetup.TREND_PULLBACK,
        state=PlanState.READY,
        decision_at=1_700_000_000,
        entry_low=100.0,
        entry_high=101.0,
        technical_invalidation=98.0,
        hard_stop=97.0,
        target_1=106.0,
        target_2=110.0,
        estimated_round_trip_cost_pct=0.2,
        expires_at=1_700_003_600,
        evidence=("4H_BULLISH", "1H_SUPPORT_ZONE"),
    )
    values.update(overrides)
    return TradePlan(**values)


def test_trade_plan_enforces_long_geometry_and_never_authorizes_trade():
    plan = _valid_plan()
    assert plan.can_authorize_trade is False
    assert plan.net_reward_risk(1) > 1.0
    assert plan.net_reward_risk(2) > plan.net_reward_risk(1)

    with pytest.raises(ValueError, match="invalidation geometry"):
        _valid_plan(hard_stop=99.0)
    with pytest.raises(ValueError, match="target geometry"):
        _valid_plan(target_1=100.5)
    with pytest.raises(ValueError, match="cannot authorize"):
        _valid_plan(can_authorize_trade=True)


def test_radar_scope_is_strictly_btc_and_eth():
    assert _valid_plan(symbol="ETHUSDT").symbol == "ETHUSDT"
    with pytest.raises(ValueError, match="restricted"):
        _valid_plan(symbol="SOLUSDT")
