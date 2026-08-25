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

from .tactical_long import (
    Candle,
    DataQualityError,
    PlanState,
    StructureState,
    SwingKind,
    TradePlan,
    atr,
    classify_structure,
    confirmed_swings,
    visible_closed_candles,
)
from .tactical_long_data import TacticalMarketSnapshot, TacticalTimeframe


class ThesisState(str, Enum):
    LONG_ALLOWED = "LONG_ALLOWED"
    OBSERVE = "OBSERVE"
    NO_LONG = "NO_LONG"


@dataclass(frozen=True)
class MediumHorizonLongContext:
    symbol: str
    decision_at: int
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
    """Reduce exposure in unusually volatile regimes; never tighten the stop."""
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


def _validated_frame(
    snapshot: TacticalMarketSnapshot,
    symbol: str,
    timeframe: TacticalTimeframe,
) -> tuple[Candle, ...]:
    """Revalidate the thesis frame at the exact replay/live decision cut.

    TacticalMarketSnapshot can also be manually reconstructed, so the policy
    layer must not assume ingestion-time validation has already happened.
    """
    return visible_closed_candles(
        snapshot.candles[symbol][timeframe],
        decision_at=snapshot.decision_at,
        max_staleness_seconds=timeframe.seconds * 2,
    )


def evaluate_medium_horizon_long(snapshot: TacticalMarketSnapshot, symbol: str) -> MediumHorizonLongContext:
    """Build the thesis from point-in-time-valid D1/H4 structure before execution noise."""
    symbol = symbol.upper()
    if symbol not in {"BTCUSDT", "ETHUSDT"}:
        raise ValueError("medium-horizon policy is restricted to BTCUSDT and ETHUSDT")

    d1 = _validated_frame(snapshot, symbol, TacticalTimeframe.D1)
    h4 = _validated_frame(snapshot, symbol, TacticalTimeframe.H4)
    daily_state, _ = _structure(d1, TacticalTimeframe.D1)
    h4_state, h4_swings = _structure(h4, TacticalTimeframe.H4)
    protected_low = _protected_h4_low(h4_swings)
    scalar = _volatility_exposure_scalar(d1)
    evidence = [f"D1_STRUCTURE_{daily_state.value}", f"H4_STRUCTURE_{h4_state.value}"]
    reasons: list[str] = []

    def context(state: ThesisState, *, invalidation=protected_low, reasons_out=()) -> MediumHorizonLongContext:
        return MediumHorizonLongContext(
            symbol=symbol,
            decision_at=snapshot.decision_at,
            state=state,
            daily_structure=daily_state,
            h4_structure=h4_state,
            structural_invalidation=invalidation,
            volatility_exposure_scalar=scalar,
            reasons=tuple(reasons_out),
            evidence=tuple(evidence + (["CONFIRMED_H4_PROTECTED_LOW"] if invalidation is not None else [])),
        )

    if daily_state is StructureState.BEARISH:
        return context(ThesisState.NO_LONG, reasons_out=("DAILY_STRUCTURE_BEARISH",))
    if daily_state is StructureState.INSUFFICIENT_DATA:
        return context(ThesisState.NO_LONG, reasons_out=("INSUFFICIENT_DAILY_STRUCTURE",))
    if h4_state is StructureState.BEARISH:
        return context(ThesisState.NO_LONG, reasons_out=("H4_STRUCTURE_BEARISH",))
    if protected_low is None:
        return context(ThesisState.NO_LONG, invalidation=None, reasons_out=("NO_CONFIRMED_H4_PROTECTED_LOW",))

    if daily_state is StructureState.BULLISH:
        if scalar < 1.0:
            reasons.append("ELEVATED_VOLATILITY_REDUCE_EXPOSURE")
        return context(ThesisState.LONG_ALLOWED, reasons_out=tuple(reasons))
    return context(ThesisState.OBSERVE, reasons_out=("DAILY_RANGE_OR_TRANSITION",))


def validate_execution_plan(plan: TradePlan, context: MediumHorizonLongContext) -> tuple[bool, tuple[str, ...]]:
    """Reject execution plans that contradict or outlive the medium-horizon thesis."""
    reasons: list[str] = []
    if plan.symbol != context.symbol:
        reasons.append("PLAN_CONTEXT_SYMBOL_MISMATCH")
    if plan.decision_at != context.decision_at:
        reasons.append("PLAN_CONTEXT_DECISION_TIME_MISMATCH")
    if plan.state is PlanState.INVALIDATED:
        reasons.append("EXECUTION_PLAN_INVALIDATED")
    if context.state is not ThesisState.LONG_ALLOWED:
        reasons.append("MEDIUM_HORIZON_LONG_NOT_ALLOWED")
    if context.structural_invalidation is None:
        reasons.append("STRUCTURAL_INVALIDATION_UNKNOWN")
    elif plan.hard_stop >= context.structural_invalidation:
        reasons.append("HARD_STOP_INSIDE_H4_STRUCTURE")
    if reasons:
        return False, tuple(reasons)
    return True, ()
