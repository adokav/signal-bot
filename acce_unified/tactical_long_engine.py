"""Deterministic setup detection for the BTC/ETH tactical Long radar.

This module is deliberately advisory.  It turns a complete MEXC snapshot into
zero or one structural Long plan per symbol.  It never sizes or submits orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Mapping, Sequence

from .tactical_long import (
    Candle,
    DataQualityError,
    PlanState,
    PriceZone,
    StructureState,
    TacticalSetup,
    TradePlan,
    atr,
    classify_structure,
    cluster_price_zones,
    confirmed_swings,
)
from .tactical_long_data import (
    Quote,
    TacticalMarketSnapshot,
    TacticalTimeframe,
)


@dataclass(frozen=True)
class TacticalAssessment:
    symbol: str
    decision_at: int
    structure_4h: StructureState
    setup: TacticalSetup | None
    state: PlanState | None
    plan: TradePlan | None
    reasons: tuple[str, ...]
    evidence: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()

    @property
    def has_actionable_plan(self) -> bool:
        return self.plan is not None and self.state in {PlanState.READY, PlanState.TRIGGERED}

    def to_dict(self) -> dict[str, object]:
        plan = self.plan
        return {
            "symbol": self.symbol,
            "decision_at": self.decision_at,
            "structure_4h": self.structure_4h.value,
            "setup": self.setup.value if self.setup else None,
            "state": self.state.value if self.state else "NO_LONG",
            "reasons": list(self.reasons),
            "evidence": list(self.evidence),
            "risk_flags": list(self.risk_flags),
            "plan": None if plan is None else {
                "entry_low": plan.entry_low,
                "entry_high": plan.entry_high,
                "technical_invalidation": plan.technical_invalidation,
                "hard_stop": plan.hard_stop,
                "target_1": plan.target_1,
                "target_2": plan.target_2,
                "net_rr_1": plan.net_reward_risk(1),
                "net_rr_2": plan.net_reward_risk(2),
                "expires_at": plan.expires_at,
                "estimated_round_trip_cost_pct": plan.estimated_round_trip_cost_pct,
            },
            "can_authorize_trade": False,
        }


@dataclass(frozen=True)
class TacticalRadarReport:
    generated_at: int
    assessments: tuple[TacticalAssessment, ...]
    source: str
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "source": self.source,
            "assessments": [item.to_dict() for item in self.assessments],
            "errors": list(self.errors),
            "can_authorize_trade": False,
        }


def _ema(candles: Sequence[Candle], period: int) -> float:
    if period <= 0 or len(candles) < period:
        raise DataQualityError("insufficient candles for EMA")
    alpha = 2.0 / (period + 1.0)
    value = candles[-period].close
    for row in candles[-period + 1:]:
        value = alpha * row.close + (1.0 - alpha) * value
    return value


def _volume_ratio(candles: Sequence[Candle], lookback: int = 20) -> float:
    if len(candles) < lookback + 1:
        return 0.0
    baseline = median(row.volume for row in candles[-lookback - 1:-1])
    return candles[-1].volume / baseline if baseline > 0 else 0.0


def _bullish_confirmation(candles: Sequence[Candle]) -> bool:
    if len(candles) < 3:
        return False
    row = candles[-1]
    previous = candles[-2]
    body = abs(row.close - row.open)
    span = max(row.high - row.low, 1e-12)
    return (
        row.close > row.open
        and row.close > previous.high
        and body / span >= 0.45
        and _volume_ratio(candles) >= 0.85
    )


def _recent_swings(candles: Sequence[Candle], timeframe: TacticalTimeframe):
    local_atr = atr(candles, 14)
    left_right = 3 if timeframe in {TacticalTimeframe.M5, TacticalTimeframe.M15} else 2
    move_multiplier = {
        TacticalTimeframe.M5: 0.25,
        TacticalTimeframe.M15: 0.35,
        TacticalTimeframe.H1: 0.50,
        TacticalTimeframe.H4: 0.75,
        TacticalTimeframe.D1: 1.00,
    }[timeframe]
    return confirmed_swings(
        candles,
        left=left_right,
        right=left_right,
        minimum_move=local_atr * move_multiplier,
    )


def _zones(candles: Sequence[Candle], timeframe: TacticalTimeframe) -> tuple[PriceZone, ...]:
    swings = _recent_swings(candles, timeframe)
    tolerance = atr(candles, 14) * (0.12 if timeframe is TacticalTimeframe.H1 else 0.10)
    return cluster_price_zones(swings, tolerance=tolerance)


def _nearest_below(zones: Sequence[PriceZone], price: float) -> PriceZone | None:
    eligible = [zone for zone in zones if zone.midpoint < price]
    return max(eligible, key=lambda zone: zone.midpoint, default=None)


def _nearest_above(zones: Sequence[PriceZone], price: float) -> PriceZone | None:
    eligible = [zone for zone in zones if zone.midpoint > price]
    return min(eligible, key=lambda zone: zone.midpoint, default=None)


def _cost_pct(quote: Quote) -> float:
    # Conservative public-market estimate: two spread crossings plus baseline fees.
    return max(0.04, quote.spread_bps * 2.0 / 100.0 + 0.02)


def _build_plan(
    *,
    symbol: str,
    decision_at: int,
    setup: TacticalSetup,
    state: PlanState,
    entry_low: float,
    entry_high: float,
    invalidation: float,
    stop: float,
    target_1: float,
    target_2: float,
    cost_pct: float,
    expiry_seconds: int,
    evidence: Sequence[str],
    risks: Sequence[str],
    minimum_rr: float = 1.5,
) -> TradePlan | None:
    try:
        plan = TradePlan(
            symbol=symbol,
            setup=setup,
            state=state,
            decision_at=decision_at,
            entry_low=entry_low,
            entry_high=entry_high,
            technical_invalidation=invalidation,
            hard_stop=stop,
            target_1=target_1,
            target_2=target_2,
            estimated_round_trip_cost_pct=cost_pct,
            expires_at=decision_at + expiry_seconds,
            evidence=tuple(evidence),
            risk_flags=tuple(risks),
        )
    except ValueError:
        return None
    if plan.net_reward_risk(1) < minimum_rr or plan.net_reward_risk(2) < 2.0:
        return None
    return plan


class TacticalLongEngine:
    """Evaluate BTCUSDT and ETHUSDT without forcing a trade."""

    def analyze(self, snapshot: TacticalMarketSnapshot) -> TacticalRadarReport:
        results: list[TacticalAssessment] = []
        btc = self._analyze_symbol("BTCUSDT", snapshot)
        results.append(btc)
        results.append(self._analyze_symbol("ETHUSDT", snapshot, btc_context=btc))
        return TacticalRadarReport(
            generated_at=snapshot.decision_at,
            assessments=tuple(results),
            source=snapshot.source,
        )

    def _analyze_symbol(
        self,
        symbol: str,
        snapshot: TacticalMarketSnapshot,
        *,
        btc_context: TacticalAssessment | None = None,
    ) -> TacticalAssessment:
        frames: Mapping[TacticalTimeframe, tuple[Candle, ...]] = snapshot.candles[symbol]
        quote = snapshot.quotes[symbol]
        h4 = frames[TacticalTimeframe.H4]
        h1 = frames[TacticalTimeframe.H1]
        m15 = frames[TacticalTimeframe.M15]
        m5 = frames[TacticalTimeframe.M5]
        h4_atr = atr(h4, 14)
        h1_atr = atr(h1, 14)
        swings4 = _recent_swings(h4, TacticalTimeframe.H4)
        structure = classify_structure(swings4, equality_tolerance=h4_atr * 0.15)
        price = quote.midpoint
        risks: list[str] = []

        if quote.spread_bps > (8.0 if symbol == "BTCUSDT" else 12.0):
            return self._no_long(symbol, snapshot, structure, "SPREAD_TOO_WIDE")
        if structure is StructureState.BEARISH:
            return self._no_long(symbol, snapshot, structure, "4H_STRUCTURE_BEARISH")
        if structure is StructureState.INSUFFICIENT_DATA:
            return self._no_long(symbol, snapshot, structure, "INSUFFICIENT_4H_STRUCTURE")
        if symbol == "ETHUSDT" and btc_context is not None:
            if btc_context.structure_4h is StructureState.BEARISH:
                return self._no_long(symbol, snapshot, structure, "BTC_STRUCTURE_BREAKDOWN")
            ethbtc = snapshot.candles["ETHBTC"][TacticalTimeframe.H1]
            if _ema(ethbtc, 20) < _ema(ethbtc, 50) and ethbtc[-1].close < _ema(ethbtc, 20):
                risks.append("ETHBTC_RELATIVE_WEAKNESS")

        h1_zones = _zones(h1, TacticalTimeframe.H1)
        support = _nearest_below(h1_zones, price)
        resistance = _nearest_above(h1_zones, price)
        ema20 = _ema(h1, 20)
        ema50 = _ema(h1, 50)

        detectors = (
            self._detect_liquidity_sweep,
            self._detect_range_reclaim,
            self._detect_breakout_retest,
            self._detect_trend_pullback,
        )
        for detector in detectors:
            assessment = detector(
                symbol=symbol,
                snapshot=snapshot,
                structure=structure,
                price=price,
                quote=quote,
                h1=h1,
                m15=m15,
                m5=m5,
                h1_atr=h1_atr,
                ema20=ema20,
                ema50=ema50,
                support=support,
                resistance=resistance,
                inherited_risks=tuple(risks),
            )
            if assessment is not None:
                return assessment
        return self._no_long(symbol, snapshot, structure, "NO_VALID_SETUP", risks=tuple(risks))

    @staticmethod
    def _no_long(
        symbol: str,
        snapshot: TacticalMarketSnapshot,
        structure: StructureState,
        reason: str,
        *,
        risks: tuple[str, ...] = (),
    ) -> TacticalAssessment:
        return TacticalAssessment(
            symbol=symbol,
            decision_at=snapshot.decision_at,
            structure_4h=structure,
            setup=None,
            state=None,
            plan=None,
            reasons=(reason,),
            risk_flags=risks,
        )

    def _detect_trend_pullback(self, **ctx) -> TacticalAssessment | None:
        if ctx["structure"] is not StructureState.BULLISH or ctx["ema20"] <= ctx["ema50"]:
            return None
        price = ctx["price"]
        h1_atr = ctx["h1_atr"]
        if not ctx["ema20"] - h1_atr <= price <= ctx["ema20"] + 0.5 * h1_atr:
            return None
        support = ctx["support"]
        if support is None:
            return None
        entry_low = max(support.low, ctx["ema20"] - 0.20 * h1_atr)
        entry_high = min(max(support.high, entry_low * 1.0001), ctx["ema20"] + 0.20 * h1_atr)
        if entry_high <= entry_low or entry_high - entry_low > 0.45 * h1_atr:
            return None
        confirmed = _bullish_confirmation(ctx["m15"])
        state = PlanState.TRIGGERED if entry_low <= price <= entry_high and confirmed else PlanState.READY if price <= entry_high + 0.35 * h1_atr else PlanState.OBSERVE
        invalidation = support.low - 0.05 * h1_atr
        buffer = max((0.25 if ctx["symbol"] == "BTCUSDT" else 0.35) * h1_atr, ctx["quote"].ask - ctx["quote"].bid)
        target_zone = ctx["resistance"]
        if target_zone is None:
            return None
        target_1 = target_zone.low
        target_2 = max(target_zone.high, target_1 + h1_atr)
        plan = _build_plan(
            symbol=ctx["symbol"], decision_at=ctx["snapshot"].decision_at,
            setup=TacticalSetup.TREND_PULLBACK, state=state,
            entry_low=entry_low, entry_high=entry_high,
            invalidation=invalidation, stop=invalidation - buffer,
            target_1=target_1, target_2=target_2,
            cost_pct=_cost_pct(ctx["quote"]), expiry_seconds=12 * 3600,
            evidence=("4H_BULLISH_STRUCTURE", "1H_EMA_VALUE", "STRUCTURAL_SUPPORT"),
            risks=ctx["inherited_risks"],
        )
        return self._assessment_from_plan(ctx, TacticalSetup.TREND_PULLBACK, state, plan, "INSUFFICIENT_REWARD_RISK")

    def _detect_breakout_retest(self, **ctx) -> TacticalAssessment | None:
        zones = _zones(ctx["h1"][:-1], TacticalTimeframe.H1)
        prior_resistance = _nearest_below(zones, ctx["h1"][-1].close)
        if prior_resistance is None or prior_resistance.touches < 2:
            return None
        breakout = ctx["h1"][-2]
        current = ctx["h1"][-1]
        if breakout.close <= prior_resistance.high or current.low > prior_resistance.high + 0.35 * ctx["h1_atr"]:
            return None
        entry_low = prior_resistance.low
        entry_high = prior_resistance.high + 0.15 * ctx["h1_atr"]
        confirmed = current.close > prior_resistance.high and _bullish_confirmation(ctx["m15"])
        state = PlanState.TRIGGERED if entry_low <= ctx["price"] <= entry_high and confirmed else PlanState.READY
        invalidation = prior_resistance.low - 0.10 * ctx["h1_atr"]
        buffer = (0.25 if ctx["symbol"] == "BTCUSDT" else 0.35) * ctx["h1_atr"]
        height = max(ctx["h1_atr"], prior_resistance.high - min(row.low for row in ctx["h1"][-48:]))
        plan = _build_plan(
            symbol=ctx["symbol"], decision_at=ctx["snapshot"].decision_at,
            setup=TacticalSetup.BREAKOUT_RETEST, state=state,
            entry_low=entry_low, entry_high=entry_high,
            invalidation=invalidation, stop=invalidation - buffer,
            target_1=prior_resistance.high + 0.50 * height,
            target_2=prior_resistance.high + height,
            cost_pct=_cost_pct(ctx["quote"]), expiry_seconds=8 * 3600,
            evidence=("CONFIRMED_BREAKOUT", "RETEST_ZONE", "CLOSED_CANDLE_CONFIRMATION"),
            risks=ctx["inherited_risks"],
        )
        return self._assessment_from_plan(ctx, TacticalSetup.BREAKOUT_RETEST, state, plan, "INSUFFICIENT_REWARD_RISK")

    def _detect_range_reclaim(self, **ctx) -> TacticalAssessment | None:
        support = ctx["support"]
        resistance = ctx["resistance"]
        if support is None or resistance is None or support.touches < 2:
            return None
        recent = ctx["m15"][-4:]
        sweep_low = min(row.low for row in recent)
        reclaimed = sweep_low < support.low and recent[-1].close > support.high
        if not reclaimed or support.low - sweep_low > 0.75 * ctx["h1_atr"]:
            return None
        entry_low = support.low
        entry_high = support.high + 0.20 * ctx["h1_atr"]
        state = PlanState.TRIGGERED if ctx["price"] <= entry_high and _bullish_confirmation(ctx["m15"]) else PlanState.READY
        invalidation = sweep_low - 0.05 * ctx["h1_atr"]
        buffer = 0.15 * ctx["h1_atr"]
        range_mid = (support.midpoint + resistance.midpoint) / 2.0
        plan = _build_plan(
            symbol=ctx["symbol"], decision_at=ctx["snapshot"].decision_at,
            setup=TacticalSetup.RANGE_RECLAIM, state=state,
            entry_low=entry_low, entry_high=entry_high,
            invalidation=invalidation, stop=invalidation - buffer,
            target_1=range_mid, target_2=resistance.low,
            cost_pct=_cost_pct(ctx["quote"]), expiry_seconds=6 * 3600,
            evidence=("RANGE_SUPPORT", "FAILED_BREAKDOWN", "RECLAIM_CLOSE"),
            risks=ctx["inherited_risks"],
        )
        return self._assessment_from_plan(ctx, TacticalSetup.RANGE_RECLAIM, state, plan, "INSUFFICIENT_REWARD_RISK")

    def _detect_liquidity_sweep(self, **ctx) -> TacticalAssessment | None:
        swings = _recent_swings(ctx["m15"], TacticalTimeframe.M15)
        lows = [item for item in swings if item.kind.value == "LOW"]
        if not lows:
            return None
        reference = lows[-1].price
        recent = ctx["m15"][-3:]
        sweep = min(row.low for row in recent)
        last = recent[-1]
        span = max(last.high - last.low, 1e-12)
        if not (sweep < reference and reference - sweep <= 0.50 * ctx["h1_atr"] and last.close > reference):
            return None
        if (last.close - last.low) / span < 0.60:
            return None
        local_high = max(row.high for row in ctx["m5"][-12:-2])
        entry_low = reference
        entry_high = max(reference * 1.0001, min(local_high, reference + 0.25 * ctx["h1_atr"]))
        state = PlanState.TRIGGERED if ctx["price"] > local_high and _bullish_confirmation(ctx["m5"]) else PlanState.READY
        invalidation = sweep - 0.05 * ctx["h1_atr"]
        buffer = 0.20 * atr(ctx["m15"], 14)
        resistance = ctx["resistance"]
        if resistance is None:
            return None
        target_1 = max(entry_high + ctx["h1_atr"], (entry_high + resistance.low) / 2.0)
        target_2 = resistance.low
        if target_2 < target_1:
            target_2 = target_1 + ctx["h1_atr"]
        plan = _build_plan(
            symbol=ctx["symbol"], decision_at=ctx["snapshot"].decision_at,
            setup=TacticalSetup.LIQUIDITY_SWEEP_RECLAIM, state=state,
            entry_low=entry_low, entry_high=entry_high,
            invalidation=invalidation, stop=invalidation - buffer,
            target_1=target_1, target_2=target_2,
            cost_pct=_cost_pct(ctx["quote"]), expiry_seconds=4 * 3600,
            evidence=("CONFIRMED_SWING_LOW", "LIQUIDITY_SWEEP", "RECLAIM_CLOSE"),
            risks=ctx["inherited_risks"],
        )
        return self._assessment_from_plan(ctx, TacticalSetup.LIQUIDITY_SWEEP_RECLAIM, state, plan, "INSUFFICIENT_REWARD_RISK")

    @staticmethod
    def _assessment_from_plan(ctx, setup, state, plan, rejection_reason):
        if plan is None:
            return TacticalAssessment(
                symbol=ctx["symbol"], decision_at=ctx["snapshot"].decision_at,
                structure_4h=ctx["structure"], setup=setup, state=None, plan=None,
                reasons=(rejection_reason,), risk_flags=ctx["inherited_risks"],
            )
        return TacticalAssessment(
            symbol=ctx["symbol"], decision_at=ctx["snapshot"].decision_at,
            structure_4h=ctx["structure"], setup=setup, state=state, plan=plan,
            reasons=(), evidence=plan.evidence, risk_flags=plan.risk_flags,
        )
