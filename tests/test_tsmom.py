"""Tests for trading.strategies.tsmom."""

from __future__ import annotations

import math

import pytest

from trading.data.binance_perp import Candle
from trading.strategies.tsmom import (
    ExitPlan,
    TsmomParams,
    TsmomSignal,
    atr_pct,
    evaluate_tsmom,
    lookback_return_pct,
    position_scale,
    realized_annualized_vol_pct,
)


def _daily_candles(closes: list[float], start_time: int = 0) -> list[Candle]:
    day = 86_400
    out: list[Candle] = []
    for i, close in enumerate(closes):
        open_time = start_time + i * day
        close_time = open_time + day - 1
        # keep OHLC internally consistent
        high = close * 1.02
        low = close * 0.98
        open_ = close * 1.00
        out.append(
            Candle(
                open_time=open_time,
                close_time=close_time,
                available_at=close_time,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=1.0,
                quote_volume=close,
            )
        )
    return out


def test_lookback_return_pct_matches_definition():
    closes = [100.0, 101.0, 102.0, 105.0, 110.0]
    # 4-day return from 100 -> 110 = 10%
    assert lookback_return_pct(closes, lookback=4) == pytest.approx(10.0)


def test_realized_annualized_vol_scales_by_sqrt_365():
    # Alternating +1% / -1% log-ish returns; stdev ~ 1%
    closes = [100.0]
    for _ in range(30):
        closes.append(closes[-1] * 1.01)
        closes.append(closes[-1] * 0.99)
    vol = realized_annualized_vol_pct(closes, lookback=60)
    # crude sanity: annualized > 15%, < 80%
    assert 15.0 < vol < 100.0


def test_atr_pct_positive_on_random_walk():
    candles = _daily_candles([100.0 + i * 0.5 for i in range(20)])
    atr = atr_pct(candles, lookback=14)
    assert atr > 0


def test_position_scale_capped_at_max_leverage():
    scale = position_scale(realized_vol_pct=1.0, target_vol_pct=40.0, max_leverage=3.0)
    assert scale == pytest.approx(3.0)


def test_position_scale_zero_when_realized_vol_zero():
    scale = position_scale(realized_vol_pct=0.0, target_vol_pct=40.0, max_leverage=3.0)
    assert scale == 0.0


def test_evaluate_tsmom_no_trade_when_history_insufficient():
    candles = _daily_candles([100.0 + i for i in range(10)])
    decision = evaluate_tsmom(candles, symbol="BTCUSDT", decision_at=candles[-1].close_time)
    assert decision.signal is TsmomSignal.NO_TRADE
    assert "INSUFFICIENT_HISTORY" in decision.reasons


def test_evaluate_tsmom_no_trade_when_momentum_negative():
    # Strong downtrend across 90 days
    closes = [100.0 * math.exp(-0.005 * i) for i in range(120)]
    candles = _daily_candles(closes)
    decision = evaluate_tsmom(candles, symbol="BTCUSDT", decision_at=candles[-1].close_time)
    assert decision.signal is TsmomSignal.NO_TRADE
    assert "MOMENTUM_NEGATIVE_OR_FLAT" in decision.reasons


def test_evaluate_tsmom_long_when_momentum_positive_and_vol_ok():
    # Steady uptrend across 120 days
    closes = [100.0 * math.exp(0.003 * i) for i in range(150)]
    candles = _daily_candles(closes)
    decision = evaluate_tsmom(
        candles,
        symbol="BTCUSDT",
        decision_at=candles[-1].close_time,
        params=TsmomParams(target_annualized_vol_pct=40.0, max_leverage=3.0),
    )
    assert decision.signal is TsmomSignal.LONG
    assert decision.plan is not None
    assert decision.entry_price == pytest.approx(closes[-1])
    # ExitPlan geometry invariants
    plan = decision.plan
    assert plan.hard_stop < plan.entry_price < plan.target_1 <= plan.target_2
    assert decision.position_scale > 0
    assert decision.can_authorize_trade is False


def test_exit_plan_rejects_invalid_geometry():
    with pytest.raises(ValueError):
        ExitPlan(
            entry_price=100.0,
            hard_stop=101.0,  # stop above entry — inverted
            target_1=105.0,
            target_2=110.0,
            time_stop_seconds=3600,
        )
