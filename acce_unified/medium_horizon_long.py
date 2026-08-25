"""Medium-horizon BTC/ETH long thesis and risk policy.

This module deliberately separates *trade thesis* from execution patterns.
M5/M15 patterns may refine execution, but they cannot create a long thesis or
move the structural hard stop inward. Volatility changes exposure, not the
price level that proves the thesis wrong.

Research/advisory only: no order authority and no account-risk assumptions.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import sqrt
from statistics import pstdev
from typing import Sequence

from .tactical_long import Candle, DataQualityError, StructureState, SwingKind, TradePlan, atr, classify_structure, confirmed_swings
from .tactical_long_data import TacticalMarketSnapshot, TacticalTimeframe


class ThesisState(str, Enum):
    LONG_ALLOWED = "LONG_ALLOWED"
    OBSERVE = "OBSERVE"
    NO_LONG = "NO_LONG"


@dataclass(frozen=True)
class MediumHorizonLongContext:
    symbol: str
    state: ThesisState
    daily_structure: StructureState
    h4_structure: StructureState
    structural_invalidation: float | None
    volatility_exposure_scalar: float
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]

    @property
    def can_authorize_trade(self) -> bool:
        return False


def _structure(candles: Sequence[Candle], timeframe: TacticalTimeframe) -> tuple[StructureState, tuple]:
    local_atr = atr(candles, 14)
    multiplier = 1.0 if timeframe is TacticalTimeframe.D1 else 0.75
    swings = confirmed_swings(candles, left=2, right=2, minimum_move=local_atr * multiplier)
    state = classify_structure(swings, equality_tolerance=local_atr * 0.15)
    return state, swings


def _realized_vol_pct(candles: Sequence[Candle], lookback: int) -> float:
    if len(candles) < lookback + 1:
        raise DataQualityError("insufficient candles for realized volatility")
    closes = [row.close for row in candles[-lookback - 1:]]
    returns = [(closes[i] / closes[i - 1] - 1.0) for i in range(1, len(closes))]
    return pstdev(returns) * sqrt(365.0) * 100.0


def _volatility_exposure_scalar(d1: Sequence[Candle]) -> float:
    """Reduce exposure in unusually volatile regimes; never tighten the stop.

    This is a dimensionless research scalar, not position sizing. The fast
    20-day realized volatility is compared with the slower 60-day baseline.
    """
    fast = _realized_vol_pct(d1, 20)
    slow = _realized_vol_pct(d1, 60)
    if slow <= 0:
        return 0.5
    ratio = fast / slow
    if ratio >= 1.75:
        return 0.40
    if ratio >= 1.35:
        return 0.60
    if ratio >= 1.10:
        return 0.80
    return 1.0


def _protected_h4_low(swings: Sequence) -> float | None:
    lows = [item for item in swings if item.kind is SwingKind.LOW]
    return lows[-1].price if lows else None


def evaluate_medium_horizon_long(snapshot: TacticalMarketSnapshot, symbol: str) -> MediumHorizonLongContext:
    """Build the thesis from D1/H4 structure before looking at execution noise."""
    symbol = symbol.upper()
    if symbol not in {"BTCUSDT", "ETHUSDT"}:
        raise ValueError("medium-horizon policy is restricted to BTCUSDT and ETHUSDT")
    frames = snapshot.candles[symbol]
    d1 = frames[TacticalTimeframe.D1]
    h4 = frames[TacticalTimeframe.H4]
    daily_state, _ = _structure(d1, TacticalTimeframe.D1)
    h4_state, h4_swings = _structure(h4, TacticalTimeframe.H4)
    protected_low = _protected_h4_low(h4_swings)
    scalar = _volatility_exposure_scalar(d1)
    evidence = [f"D1_STRUCTURE_{daily_state.value}", f"H4_STRUCTURE_{h4_state.value}"]
    reasons: list[str] = []

    # A lower-timeframe setup cannot overrule a bearish or unknowable daily thesis.
    if daily_state is StructureState.BEARISH:
        return MediumHorizonLongContext(symbol, ThesisState.NO_LONG, daily_state, h4_state, protected_low, scalar,
                                        ("DAILY_STRUCTURE_BEARISH",), tuple(evidence))
    if daily_state is StructureState.INSUFFICIENT_DATA:
        return MediumHorizonLongContext(symbol, ThesisState.NO_LONG, daily_state, h4_state, protected_low, scalar,
                                        ("INSUFFICIENT_DAILY_STRUCTURE",), tuple(evidence))
    if h4_state is StructureState.BEARISH:
        return MediumHorizonLongContext(symbol, ThesisState.NO_LONG, daily_state, h4_state, protected_low, scalar,
                                        ("H4_STRUCTURE_BEARISH",), tuple(evidence))
    if protected_low is None:
        return MediumHorizonLongContext(symbol, ThesisState.NO_LONG, daily_state, h4_state, None, scalar,
                                        ("NO_CONFIRMED_H4_PROTECTED_LOW",), tuple(evidence))

    # Daily bullish + non-bearish H4 is a long thesis. Daily transition remains
    # observation-only until higher-timeframe evidence improves.
    if daily_state is StructureState.BULLISH:
        if scalar < 1.0:
            reasons.append("ELEVATED_VOLATILITY_REDUCE_EXPOSURE")
        return MediumHorizonLongContext(symbol, ThesisState.LONG_ALLOWED, daily_state, h4_state, protected_low, scalar,
                                        tuple(reasons), tuple(evidence + ["CONFIRMED_H4_PROTECTED_LOW"]))
    return MediumHorizonLongContext(symbol, ThesisState.OBSERVE, daily_state, h4_state, protected_low, scalar,
                                    ("DAILY_RANGE_OR_TRANSITION",), tuple(evidence + ["CONFIRMED_H4_PROTECTED_LOW"]))


def validate_execution_plan(plan: TradePlan, context: MediumHorizonLongContext) -> tuple[bool, tuple[str, ...]]:
    """Reject scalp-like plans that contradict the medium-horizon thesis.

    The hard stop must not sit *above* the confirmed H4 protected low. A tighter
    M5/M15 stop would allow normal lower-timeframe noise to terminate a thesis
    that has not actually failed. If the structurally valid stop makes R/R poor,
    the correct result is no trade, not a tighter stop.
    """
    reasons: list[str] = []
    if context.state is not ThesisState.LONG_ALLOWED:
        reasons.append("MEDIUM_HORIZON_LONG_NOT_ALLOWED")
    if context.structural_invalidation is None:
        reasons.append("STRUCTURAL_INVALIDATION_UNKNOWN")
    elif plan.hard_stop >= context.structural_invalidation:
        reasons.append("HARD_STOP_INSIDE_H4_STRUCTURE")
    if reasons:
        return False, tuple(reasons)
    return True, ()
