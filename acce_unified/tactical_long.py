"""Deterministic, SHADOW-only BTC/ETH tactical Long primitives.

The module deliberately stops before setup detection or trade authority.  It
provides point-in-time-safe candles, confirmed swings, price zones and an
immutable trade-plan contract that later setup detectors must satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import median
from typing import Iterable, Sequence


class DataQualityError(ValueError):
    """Raised when market data is unusable at the decision timestamp."""


class StructureState(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGE_OR_TRANSITION = "RANGE_OR_TRANSITION"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class SwingKind(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class TacticalSetup(str, Enum):
    TREND_PULLBACK = "TREND_PULLBACK"
    BREAKOUT_RETEST = "BREAKOUT_RETEST"
    RANGE_RECLAIM = "RANGE_RECLAIM"
    LIQUIDITY_SWEEP_RECLAIM = "LIQUIDITY_SWEEP_RECLAIM"


class PlanState(str, Enum):
    OBSERVE = "OBSERVE"
    READY = "READY"
    TRIGGERED = "TRIGGERED"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True)
class Candle:
    open_time: int
    close_time: int
    available_at: int
    ingested_at: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if type(self.ingested_at) is not int or self.ingested_at <= 0:
            raise DataQualityError("candle ingestion timestamp must be a positive finite integer")
        if self.open_time < 0 or self.close_time <= self.open_time:
            raise DataQualityError("invalid candle time range")
        if self.available_at < self.close_time:
            raise DataQualityError("candle cannot be available before close")
        if self.ingested_at < self.available_at:
            raise DataQualityError("candle revision cannot be ingested before provider availability")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise DataQualityError("OHLC values must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise DataQualityError("OHLC geometry is inconsistent")
        if self.volume < 0:
            raise DataQualityError("volume cannot be negative")

    def visible_at(self, decision_at: int) -> bool:
        return self.close_time <= decision_at and self.available_at <= decision_at


@dataclass(frozen=True)
class Swing:
    kind: SwingKind
    index: int
    price: float
    event_time: int
    confirmed_at: int
    available_at: int


@dataclass(frozen=True)
class PriceZone:
    low: float
    high: float
    touches: int
    first_touch_at: int
    last_touch_at: int
    member_prices: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.low <= 0 or self.high < self.low:
            raise ValueError("invalid price zone")
        if self.touches != len(self.member_prices) or self.touches <= 0:
            raise ValueError("touch count must match zone members")

    @property
    def midpoint(self) -> float:
        return (self.low + self.high) / 2.0


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    setup: TacticalSetup
    state: PlanState
    decision_at: int
    entry_low: float
    entry_high: float
    technical_invalidation: float
    hard_stop: float
    target_1: float
    target_2: float
    estimated_round_trip_cost_pct: float
    expires_at: int
    evidence: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    can_authorize_trade: bool = False

    def __post_init__(self) -> None:
        symbol = self.symbol.upper()
        if symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("tactical radar is restricted to BTCUSDT and ETHUSDT")
        if self.symbol != symbol:
            object.__setattr__(self, "symbol", symbol)
        if self.decision_at <= 0 or self.expires_at <= self.decision_at:
            raise ValueError("invalid decision/expiry timestamps")
        if min(
            self.entry_low,
            self.entry_high,
            self.technical_invalidation,
            self.hard_stop,
            self.target_1,
            self.target_2,
        ) <= 0:
            raise ValueError("all plan prices must be positive")
        if self.entry_high < self.entry_low:
            raise ValueError("entry zone is inverted")
        if not self.hard_stop < self.technical_invalidation < self.entry_low:
            raise ValueError("Long invalidation geometry is inconsistent")
        if not self.entry_high < self.target_1 <= self.target_2:
            raise ValueError("Long target geometry is inconsistent")
        if self.estimated_round_trip_cost_pct < 0:
            raise ValueError("cost cannot be negative")
        if self.can_authorize_trade:
            raise ValueError("tactical research plan cannot authorize a trade")

    @property
    def assumed_entry(self) -> float:
        return (self.entry_low + self.entry_high) / 2.0

    def net_reward_risk(self, target: int = 1) -> float:
        selected = self.target_1 if target == 1 else self.target_2
        entry = self.assumed_entry
        gross_reward_pct = (selected / entry - 1.0) * 100.0
        gross_risk_pct = (entry / self.hard_stop - 1.0) * 100.0
        net_reward_pct = gross_reward_pct - self.estimated_round_trip_cost_pct
        net_risk_pct = gross_risk_pct + self.estimated_round_trip_cost_pct
        if net_risk_pct <= 0:
            raise ValueError("net risk must be positive")
        return net_reward_pct / net_risk_pct


def visible_closed_candles(
    candles: Iterable[Candle], *, decision_at: int, max_staleness_seconds: int | None = None
) -> tuple[Candle, ...]:
    """Return chronological, point-in-time-visible completed candles.

    The function rejects mixed or future data rather than silently truncating
    it, because a provider returning an open/future row is a data-quality event.
    """
    rows = tuple(candles)
    if not rows:
        raise DataQualityError("no candles")
    if any(not row.visible_at(decision_at) for row in rows):
        raise DataQualityError("future or open candle supplied")
    if any(rows[index].open_time <= rows[index - 1].open_time for index in range(1, len(rows))):
        raise DataQualityError("candles must be strictly chronological")
    if max_staleness_seconds is not None:
        if decision_at - rows[-1].close_time > max_staleness_seconds:
            raise DataQualityError("latest candle is stale")
    return rows


def true_range(current: Candle, previous_close: float | None) -> float:
    if previous_close is None:
        return current.high - current.low
    return max(
        current.high - current.low,
        abs(current.high - previous_close),
        abs(current.low - previous_close),
    )


def atr(candles: Sequence[Candle], period: int = 14) -> float:
    if period <= 0 or len(candles) < period + 1:
        raise DataQualityError("insufficient candles for ATR")
    ranges = [
        true_range(candles[index], candles[index - 1].close)
        for index in range(1, len(candles))
    ]
    return sum(ranges[-period:]) / period


def confirmed_swings(
    candles: Sequence[Candle],
    *,
    left: int,
    right: int,
    minimum_move: float,
) -> tuple[Swing, ...]:
    """Detect confirmed fractal swings without using unconfirmed right-edge data."""
    if left <= 0 or right <= 0 or minimum_move < 0:
        raise ValueError("invalid swing parameters")
    if len(candles) < left + right + 1:
        return ()
    candidates: list[Swing] = []
    for index in range(left, len(candles) - right):
        row = candles[index]
        left_rows = candles[index - left:index]
        right_rows = candles[index + 1:index + right + 1]
        is_high = all(row.high > item.high for item in (*left_rows, *right_rows))
        is_low = all(row.low < item.low for item in (*left_rows, *right_rows))
        confirmed_at = candles[index + right].close_time
        available_at = max(item.available_at for item in candles[index:index + right + 1])
        if is_high:
            candidates.append(Swing(
                kind=SwingKind.HIGH,
                index=index,
                price=row.high,
                event_time=row.close_time,
                confirmed_at=confirmed_at,
                available_at=available_at,
            ))
        if is_low:
            candidates.append(Swing(
                kind=SwingKind.LOW,
                index=index,
                price=row.low,
                event_time=row.close_time,
                confirmed_at=confirmed_at,
                available_at=available_at,
            ))
    filtered: list[Swing] = []
    for swing in sorted(candidates, key=lambda item: (item.index, item.kind.value)):
        if not filtered:
            filtered.append(swing)
            continue
        previous = filtered[-1]
        if swing.kind is previous.kind:
            more_extreme = (
                swing.price > previous.price
                if swing.kind is SwingKind.HIGH
                else swing.price < previous.price
            )
            if more_extreme:
                filtered[-1] = swing
            continue
        if abs(swing.price - previous.price) < minimum_move:
            continue
        filtered.append(swing)
    return tuple(filtered)


def classify_structure(swings: Sequence[Swing], *, equality_tolerance: float) -> StructureState:
    highs = [item for item in swings if item.kind is SwingKind.HIGH]
    lows = [item for item in swings if item.kind is SwingKind.LOW]
    if len(highs) < 2 or len(lows) < 2:
        return StructureState.INSUFFICIENT_DATA
    high_delta = highs[-1].price - highs[-2].price
    low_delta = lows[-1].price - lows[-2].price
    if high_delta > equality_tolerance and low_delta > equality_tolerance:
        return StructureState.BULLISH
    if high_delta < -equality_tolerance and low_delta < -equality_tolerance:
        return StructureState.BEARISH
    return StructureState.RANGE_OR_TRANSITION


def cluster_price_zones(
    swings: Sequence[Swing], *, tolerance: float
) -> tuple[PriceZone, ...]:
    if tolerance <= 0:
        raise ValueError("zone tolerance must be positive")
    ordered = sorted(swings, key=lambda item: item.price)
    if not ordered:
        return ()
    clusters: list[list[Swing]] = []
    for swing in ordered:
        if not clusters:
            clusters.append([swing])
            continue
        center = median(item.price for item in clusters[-1])
        if abs(swing.price - center) <= tolerance:
            clusters[-1].append(swing)
        else:
            clusters.append([swing])
    zones = []
    for members in clusters:
        prices = tuple(item.price for item in members)
        zones.append(PriceZone(
            low=min(prices),
            high=max(prices),
            touches=len(members),
            first_touch_at=min(item.event_time for item in members),
            last_touch_at=max(item.event_time for item in members),
            member_prices=prices,
        ))
    return tuple(zones)
