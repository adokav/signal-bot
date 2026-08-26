from __future__ import annotations

from acce_unified.tactical_long import Candle, PlanState, TacticalSetup
from acce_unified.tactical_long_data import (
    CONTEXT_SYMBOLS,
    REQUIRED_TIMEFRAMES,
    TACTICAL_SYMBOLS,
    Quote,
    TacticalMarketSnapshot,
)
from acce_unified.tactical_long_engine import TacticalLongEngine, _build_plan


def _series(*, start: float, step: float, seconds: int, count: int = 240):
    rows = []
    base_time = 1_700_000_000
    amplitude = start * 0.002
    half_range = start * 0.001
    for i in range(count):
        wave = amplitude if i % 6 == 2 else -amplitude if i % 6 == 5 else 0.0
        center = start + step * i + wave
        open_time = base_time + i * seconds
        rows.append(Candle(
            open_time=open_time,
            close_time=open_time + seconds,
            available_at=open_time + seconds,
            open=center - half_range * 0.2,
            high=center + half_range,
            low=center - half_range,
            close=center + half_range * 0.2,
            volume=1000 + (i % 20) * 10,
        ))
    return tuple(rows)


def _snapshot(*, btc_step: float = 0.05, eth_step: float = 0.04):
    decision_at = 1_700_000_000 + 240 * 86_400 + 10
    candles = {}
    for symbol, start, step in (
        ("BTCUSDT", 50_000.0, btc_step),
        ("ETHUSDT", 3_000.0, eth_step),
        ("ETHBTC", 0.06, 0.000001),
    ):
        candles[symbol] = {
            frame: _series(start=start, step=step, seconds=frame.seconds)
            for frame in REQUIRED_TIMEFRAMES
        }
    quotes = {
        "BTCUSDT": Quote("BTCUSDT", decision_at, decision_at, 50_010.0, 50_011.0),
        "ETHUSDT": Quote("ETHUSDT", decision_at, decision_at, 3_010.0, 3_010.2),
        "ETHBTC": Quote("ETHBTC", decision_at, decision_at, 0.0601, 0.06011),
    }
    return TacticalMarketSnapshot(
        decision_at=decision_at,
        evidence_ingested_at=decision_at,
        candles=candles,
        quotes=quotes,
    )


def test_engine_returns_both_symbols_and_never_authorizes_trade():
    report = TacticalLongEngine().analyze(_snapshot()).to_dict()
    assert [row["symbol"] for row in report["assessments"]] == ["BTCUSDT", "ETHUSDT"]
    assert report["can_authorize_trade"] is False
    assert all(row["can_authorize_trade"] is False for row in report["assessments"])


def test_no_valid_setup_is_an_explicit_allowed_result():
    report = TacticalLongEngine().analyze(_snapshot())
    assert len(report.assessments) == 2
    for assessment in report.assessments:
        if assessment.plan is None:
            assert assessment.reasons
            assert assessment.has_actionable_plan is False


def test_plan_builder_rejects_inadequate_cost_adjusted_reward_risk():
    plan = _build_plan(
        symbol="BTCUSDT",
        decision_at=1_700_000_000,
        setup=TacticalSetup.TREND_PULLBACK,
        state=PlanState.READY,
        entry_low=100.0,
        entry_high=101.0,
        invalidation=99.0,
        stop=98.0,
        target_1=102.0,
        target_2=103.0,
        cost_pct=0.5,
        expiry_seconds=3600,
        evidence=("TEST",),
        risks=(),
    )
    assert plan is None


def test_snapshot_contract_requires_exact_symbol_and_timeframe_coverage():
    snapshot = _snapshot()
    assert set(snapshot.candles) == {*TACTICAL_SYMBOLS, *CONTEXT_SYMBOLS}
    assert all(set(frames) == set(REQUIRED_TIMEFRAMES) for frames in snapshot.candles.values())