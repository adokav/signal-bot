"""Medium-horizon BTC/ETH long thesis and risk policy.

This module deliberately separates *trade thesis* from execution patterns.
M5/M15 patterns may refine execution, but they cannot create a long thesis or
move the structural hard stop inward. Volatility changes exposure, not the
price level that proves the thesis wrong.

Research/advisory only: no order authority and no account-risk assumptions.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from hashlib import sha256
from math import isfinite, sqrt
from statistics import pstdev
from typing import Sequence

from .tactical_long import (
    Candle,
    DataQualityError,
    PlanState,
    StructureState,
    Swing,
    SwingKind,
    TradePlan,
    atr,
    classify_structure,
    confirmed_swings,
    visible_closed_candles,
)
from .tactical_long_data import TacticalMarketSnapshot, TacticalTimeframe


THESIS_BINDING_PREFIX = "MEDIUM_HORIZON_THESIS_SHA256="
FAST_VOL_RETURN_LOOKBACK = 20
# The adapter requests at least 61 provider rows so one forming row can be
# discarded while retaining 60 closed daily candles. Sixty closes contain 59
# close-to-close returns, hence the explicit 59-return slow baseline.
SLOW_VOL_RETURN_LOOKBACK = 59


class ThesisState(str, Enum):
    LONG_ALLOWED = "LONG_ALLOWED"
    OBSERVE = "OBSERVE"
    NO_LONG = "NO_LONG"


@dataclass(frozen=True)
class MediumHorizonLongContext:
    symbol: str
    decision_at: int
    evidence_ingested_at: int
    state: ThesisState
    daily_structure: StructureState
    h4_structure: StructureState
    structural_invalidation: float | None
    volatility_exposure_scalar: float
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]
    evidence_fingerprint: str

    @property
    def can_authorize_trade(self) -> bool:
        return False


def _structure(candles: Sequence[Candle], timeframe: TacticalTimeframe) -> tuple[StructureState, tuple[Swing, ...]]:
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
    fast = _realized_vol_pct(d1, FAST_VOL_RETURN_LOOKBACK)
    slow = _realized_vol_pct(d1, SLOW_VOL_RETURN_LOOKBACK)
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


def _protected_h4_low(swings: Sequence[Swing]) -> Swing | None:
    lows = [item for item in swings if item.kind is SwingKind.LOW]
    return lows[-1] if lows else None


def _validate_candle_values(candles: Sequence[Candle], timeframe: TacticalTimeframe) -> None:
    """Fail closed on non-finite or impossible OHLCV evidence."""
    for row in candles:
        values = (row.open, row.high, row.low, row.close, row.volume)
        if not all(isfinite(value) for value in values):
            raise DataQualityError(f"{timeframe.value} candle contains non-finite OHLCV")
        if min(row.open, row.high, row.low, row.close) <= 0 or row.volume < 0:
            raise DataQualityError(f"{timeframe.value} candle contains invalid OHLCV")
        if row.high < max(row.open, row.close) or row.low > min(row.open, row.close) or row.high < row.low:
            raise DataQualityError(f"{timeframe.value} candle OHLC geometry is inconsistent")


def _validate_cadence(candles: Sequence[Candle], timeframe: TacticalTimeframe) -> None:
    """Reject sparse, shifted, or mixed-cadence higher-timeframe evidence.

    MEXC encodes a bucket's close timestamp inclusively, so a 4H candle can be
    represented as [open, open+14399]. Replay fixtures may use an exclusive
    boundary [open, open+14400]. Both are accepted, but one frame must use one
    convention consistently. Bucket opens must still be exact UTC boundaries.
    """
    expected = timeframe.seconds
    if not candles:
        raise DataQualityError(f"no {timeframe.value} candles")

    first_duration = candles[0].close_time - candles[0].open_time
    if first_duration not in {expected - 1, expected}:
        raise DataQualityError(f"{timeframe.value} candle duration mismatch")
    inclusive_close = first_duration == expected - 1

    for index, row in enumerate(candles):
        if row.open_time % expected != 0:
            raise DataQualityError(f"{timeframe.value} candle is not UTC bucket aligned")
        duration = row.close_time - row.open_time
        required_duration = expected - 1 if inclusive_close else expected
        if duration != required_duration:
            raise DataQualityError(f"{timeframe.value} candle duration mismatch")
        if index:
            previous = candles[index - 1]
            if row.open_time - previous.open_time != expected:
                raise DataQualityError(f"{timeframe.value} candle cadence gap or overlap")
            expected_open = previous.close_time + 1 if inclusive_close else previous.close_time
            if row.open_time != expected_open:
                raise DataQualityError(f"{timeframe.value} candle cadence gap or overlap")


def _validate_terminal_bucket(
    candles: Sequence[Candle], *, decision_at: int, timeframe: TacticalTimeframe
) -> None:
    """Require the immediately preceding completed UTC bucket at the decision cut."""
    expected = timeframe.seconds
    latest_completed_open = (decision_at // expected) * expected - expected
    if candles[-1].open_time != latest_completed_open:
        raise DataQualityError(f"{timeframe.value} latest completed bucket is missing")


def _validated_frame(
    snapshot: TacticalMarketSnapshot,
    symbol: str,
    timeframe: TacticalTimeframe,
) -> tuple[Candle, ...]:
    """Revalidate the thesis frame at the exact replay/live decision cut."""
    rows = visible_closed_candles(
        snapshot.candles[symbol][timeframe],
        decision_at=snapshot.decision_at,
        max_staleness_seconds=timeframe.seconds * 2,
    )
    _validate_candle_values(rows, timeframe)
    _validate_cadence(rows, timeframe)
    _validate_terminal_bucket(rows, decision_at=snapshot.decision_at, timeframe=timeframe)
    return rows


def _post_confirmation_h4_rows(h4: Sequence[Candle], protected_swing: Swing) -> tuple[Candle, ...]:
    """Return every H4 row closed after the protected low became confirmed."""
    return tuple(row for row in h4 if row.close_time > protected_swing.confirmed_at)


def _valid_fingerprint(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _thesis_evidence_fingerprint(
    *,
    source: str,
    symbol: str,
    decision_at: int,
    evidence_ingested_at: int,
    d1: Sequence[Candle],
    h4: Sequence[Candle],
) -> str:
    """Hash the exact point-in-time D1/H4 evidence vintage used by the thesis.

    The digest includes source, cut, actual first-seen ingestion time,
    timestamps, availability and every OHLCV value. Content identity alone is
    insufficient: a provider correction first downloaded after a historical cut
    is rejected before hashing and cannot be backdated into replay.
    """
    digest = sha256()
    digest.update(
        f"medium-horizon-v2|{source}|{symbol}|{decision_at}|{evidence_ingested_at}".encode("utf-8")
    )
    for timeframe, rows in ((TacticalTimeframe.D1, d1), (TacticalTimeframe.H4, h4)):
        for row in rows:
            digest.update(
                (
                    f"|{timeframe.value}|{row.open_time}|{row.close_time}|{row.available_at}|"
                    f"{float(row.open).hex()}|{float(row.high).hex()}|{float(row.low).hex()}|"
                    f"{float(row.close).hex()}|{float(row.volume).hex()}"
                ).encode("ascii")
            )
    return digest.hexdigest()


def _plan_thesis_bindings(plan: TradePlan) -> tuple[str, ...]:
    return tuple(
        item
        for item in plan.evidence
        if isinstance(item, str) and item.startswith(THESIS_BINDING_PREFIX)
    )


def bind_execution_plan(plan: TradePlan, context: MediumHorizonLongContext) -> TradePlan:
    """Return an immutable plan copy bound to one exact thesis evidence vintage.

    Existing bindings cannot be replaced. A caller that wants to act on a new
    evidence vintage must create a new plan rather than mutating/reviving the old
    setup lineage.
    """
    if plan.symbol != context.symbol:
        raise ValueError("cannot bind plan to a different symbol thesis")
    if plan.decision_at != context.decision_at:
        raise ValueError("cannot bind plan to a different decision cut")
    if type(context.evidence_ingested_at) is not int or context.evidence_ingested_at <= 0:
        raise ValueError("cannot bind plan to malformed thesis ingestion time")
    if context.evidence_ingested_at > context.decision_at:
        raise ValueError("cannot bind plan to evidence first seen after decision cut")
    if not _valid_fingerprint(context.evidence_fingerprint):
        raise ValueError("cannot bind plan to malformed thesis fingerprint")
    if any(not isinstance(item, str) for item in plan.evidence):
        raise ValueError("cannot bind plan with malformed evidence")

    expected = f"{THESIS_BINDING_PREFIX}{context.evidence_fingerprint}"
    existing = _plan_thesis_bindings(plan)
    if existing:
        if len(existing) != 1 or existing[0] != expected:
            raise ValueError("plan already carries a different or ambiguous thesis binding")
        return plan
    return replace(plan, evidence=plan.evidence + (expected,))


def evaluate_medium_horizon_long(snapshot: TacticalMarketSnapshot, symbol: str) -> MediumHorizonLongContext:
    """Build the thesis from point-in-time-valid D1/H4 structure before execution noise."""
    symbol = symbol.upper()
    if symbol not in {"BTCUSDT", "ETHUSDT"}:
        raise ValueError("medium-horizon policy is restricted to BTCUSDT and ETHUSDT")
    if type(snapshot.evidence_ingested_at) is not int or snapshot.evidence_ingested_at <= 0:
        raise DataQualityError("invalid evidence ingestion vintage")
    if snapshot.evidence_ingested_at > snapshot.decision_at:
        raise DataQualityError("evidence vintage first seen after decision cut")

    d1 = _validated_frame(snapshot, symbol, TacticalTimeframe.D1)
    h4 = _validated_frame(snapshot, symbol, TacticalTimeframe.H4)
    fingerprint = _thesis_evidence_fingerprint(
        source=snapshot.source,
        symbol=symbol,
        decision_at=snapshot.decision_at,
        evidence_ingested_at=snapshot.evidence_ingested_at,
        d1=d1,
        h4=h4,
    )
    daily_state, _ = _structure(d1, TacticalTimeframe.D1)
    h4_state, h4_swings = _structure(h4, TacticalTimeframe.H4)
    protected_swing = _protected_h4_low(h4_swings)
    protected_low = protected_swing.price if protected_swing is not None else None
    scalar = _volatility_exposure_scalar(d1)
    evidence = [f"D1_STRUCTURE_{daily_state.value}", f"H4_STRUCTURE_{h4_state.value}"]
    reasons: list[str] = []

    def context(state: ThesisState, *, invalidation=protected_low, reasons_out=()) -> MediumHorizonLongContext:
        return MediumHorizonLongContext(
            symbol=symbol,
            decision_at=snapshot.decision_at,
            evidence_ingested_at=snapshot.evidence_ingested_at,
            state=state,
            daily_structure=daily_state,
            h4_structure=h4_state,
            structural_invalidation=invalidation,
            volatility_exposure_scalar=scalar,
            reasons=tuple(reasons_out),
            evidence=tuple(evidence + (["CONFIRMED_H4_PROTECTED_LOW"] if invalidation is not None else [])),
            evidence_fingerprint=fingerprint,
        )

    if daily_state is StructureState.BEARISH:
        return context(ThesisState.NO_LONG, reasons_out=("DAILY_STRUCTURE_BEARISH",))
    if daily_state is StructureState.INSUFFICIENT_DATA:
        return context(ThesisState.NO_LONG, reasons_out=("INSUFFICIENT_DAILY_STRUCTURE",))
    if h4_state is StructureState.BEARISH:
        return context(ThesisState.NO_LONG, reasons_out=("H4_STRUCTURE_BEARISH",))
    if h4_state is StructureState.INSUFFICIENT_DATA:
        return context(ThesisState.NO_LONG, reasons_out=("INSUFFICIENT_H4_STRUCTURE",))
    if protected_swing is None:
        return context(ThesisState.NO_LONG, invalidation=None, reasons_out=("NO_CONFIRMED_H4_PROTECTED_LOW",))

    post_confirmation = _post_confirmation_h4_rows(h4, protected_swing)
    if any(row.close <= protected_low for row in post_confirmation):
        # A confirmed higher-timeframe structural break stays broken. A later
        # recovery cannot silently reactivate the same protected-low thesis.
        return context(ThesisState.NO_LONG, reasons_out=("H4_PROTECTED_LOW_CLOSE_BREAK",))

    if any(row.low <= protected_low for row in post_confirmation):
        # A wick/sweep below the protected low is not automatically a destroyed
        # thesis, but the same protected-low thesis stays observation-only until
        # a newer H4 protected low is itself confirmed.
        return context(ThesisState.OBSERVE, reasons_out=("H4_PROTECTED_LOW_WICK_BREACH",))

    if daily_state is StructureState.BULLISH:
        if scalar < 1.0:
            reasons.append("ELEVATED_VOLATILITY_REDUCE_EXPOSURE")
        return context(ThesisState.LONG_ALLOWED, reasons_out=tuple(reasons))
    return context(ThesisState.OBSERVE, reasons_out=("DAILY_RANGE_OR_TRANSITION",))


def validate_execution_plan(
    plan: TradePlan,
    context: MediumHorizonLongContext,
    *,
    evaluated_at: int,
) -> tuple[bool, tuple[str, ...]]:
    """Validate one immutable plan/context decision cut and evidence vintage.

    A READY/TRIGGERED plan is never re-authorized at a later cut or against a
    reconstructed same-cut evidence vintage. Later scans or corrected evidence
    must build a new context and a newly bound plan.
    """
    reasons: list[str] = []
    evaluation_is_valid = type(evaluated_at) is int and evaluated_at > 0
    if not evaluation_is_valid:
        reasons.append("INVALID_EXECUTION_EVALUATION_TIME")
    elif evaluated_at != plan.decision_at or evaluated_at != context.decision_at:
        reasons.append("PLAN_VALIDATION_CUT_MISMATCH")

    vintage_valid = type(context.evidence_ingested_at) is int and context.evidence_ingested_at > 0
    if not vintage_valid or (vintage_valid and context.evidence_ingested_at > context.decision_at):
        reasons.append("THESIS_EVIDENCE_VINTAGE_UNAVAILABLE_AT_CUT")

    fingerprint_valid = _valid_fingerprint(context.evidence_fingerprint)
    if not fingerprint_valid:
        reasons.append("THESIS_EVIDENCE_FINGERPRINT_INVALID")
    if any(not isinstance(item, str) for item in plan.evidence):
        reasons.append("PLAN_EVIDENCE_MALFORMED")
    bindings = _plan_thesis_bindings(plan)
    if len(bindings) != 1:
        reasons.append("THESIS_BINDING_MISSING_OR_AMBIGUOUS")
    elif fingerprint_valid and bindings[0] != f"{THESIS_BINDING_PREFIX}{context.evidence_fingerprint}":
        reasons.append("THESIS_EVIDENCE_FINGERPRINT_MISMATCH")

    if plan.symbol != context.symbol:
        reasons.append("PLAN_CONTEXT_SYMBOL_MISMATCH")
    if plan.state is PlanState.INVALIDATED:
        reasons.append("EXECUTION_PLAN_INVALIDATED")
    elif plan.state not in {PlanState.READY, PlanState.TRIGGERED}:
        reasons.append("EXECUTION_PLAN_NOT_ACTIONABLE")
    elif evaluation_is_valid and plan.state is PlanState.READY and evaluated_at >= plan.expires_at:
        reasons.append("EXECUTION_PLAN_EXPIRED")
    if context.state is not ThesisState.LONG_ALLOWED:
        reasons.append("MEDIUM_HORIZON_LONG_NOT_ALLOWED")

    invalidation = context.structural_invalidation
    if invalidation is None or not isfinite(invalidation) or invalidation <= 0:
        reasons.append("STRUCTURAL_INVALIDATION_UNKNOWN")
    elif plan.hard_stop >= invalidation:
        reasons.append("HARD_STOP_INSIDE_H4_STRUCTURE")

    if reasons:
        return False, tuple(reasons)
    return True, ()