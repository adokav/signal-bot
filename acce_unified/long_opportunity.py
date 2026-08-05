"""Setup-aware, SHADOW-only contracts for the Liquid 100 Long engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LongSetup(str, Enum):
    TREND_PULLBACK = "TREND_PULLBACK"
    BREAKOUT_CONFIRMATION = "BREAKOUT_CONFIRMATION"
    RELATIVE_STRENGTH = "RELATIVE_STRENGTH"
    LIQUIDITY_SWEEP_RECLAIM = "LIQUIDITY_SWEEP_RECLAIM"
    NO_VALID_SETUP = "NO_VALID_SETUP"


class PathOutcome(str, Enum):
    TP_BEFORE_SL = "TP_BEFORE_SL"
    SL_BEFORE_TP = "SL_BEFORE_TP"
    TIMEOUT = "TIMEOUT"
    INVALID = "INVALID"


HARD_VETO_CODES = {
    "INVALID_DATA",
    "PROVIDER_UNAVAILABLE",
    "EXTREME_SPREAD",
    "INSUFFICIENT_LIQUIDITY",
    "MARKET_SHOCK",
    "CAPITULATION",
}


@dataclass(frozen=True)
class LongFunnelDecision:
    symbol: str
    setup: LongSetup
    hard_vetoes: tuple[str, ...] = ()
    soft_rejections: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    current_rule_score: int | None = None
    conservative_edge: float | None = None
    can_authorize_trade: bool = False

    @property
    def accepted_for_shadow(self) -> bool:
        return (
            not self.hard_vetoes
            and not self.soft_rejections
            and self.setup is not LongSetup.NO_VALID_SETUP
        )


@dataclass(frozen=True)
class LongPathLabel:
    symbol: str
    setup: LongSetup
    decision_at: int
    horizon_seconds: int
    outcome: PathOutcome
    entry_price: float
    stop_price: float
    target_price: float
    mfe_pct: float
    mae_pct: float
    net_return_pct: float
    holding_seconds: int
    label_version: str = "long-path-v1"

    def __post_init__(self) -> None:
        if self.decision_at <= 0 or self.horizon_seconds <= 0:
            raise ValueError("decision_at and horizon_seconds must be positive")
        if min(self.entry_price, self.stop_price, self.target_price) <= 0:
            raise ValueError("entry, stop and target prices must be positive")
        if self.holding_seconds < 0 or self.holding_seconds > self.horizon_seconds:
            raise ValueError("holding_seconds must be inside the label horizon")


def classify_setup(metrics: dict[str, Any] | None) -> LongSetup:
    """Deterministic first-pass setup classifier; no trade authority."""
    data = metrics or {}
    if data.get("status") != "READY":
        return LongSetup.NO_VALID_SETUP

    ema_distance = float(data.get("ema20_distance_pct") or 0.0)
    ema_slope = float(data.get("ema20_slope_pct") or 0.0)
    change_1h = float(data.get("change_1h_pct") or 0.0)
    change_4h = float(data.get("change_4h_pct") or 0.0)
    range_position = float(data.get("range_position_pct") or 50.0)
    volume_ratio = float(data.get("volume_ratio") or 0.0)
    drawdown = float(data.get("drawdown_from_high_pct") or 0.0)

    if ema_slope > 0 and -1.5 <= ema_distance <= 2.5 and -1.0 <= change_1h <= 1.5:
        return LongSetup.TREND_PULLBACK
    if range_position >= 85 and volume_ratio >= 1.5 and change_1h > 0:
        return LongSetup.BREAKOUT_CONFIRMATION
    if change_4h >= 3.0 and change_1h >= 0.5 and ema_slope > 0:
        return LongSetup.RELATIVE_STRENGTH
    if drawdown <= -4.0 and change_1h > 0 and range_position >= 55:
        return LongSetup.LIQUIDITY_SWEEP_RECLAIM
    return LongSetup.NO_VALID_SETUP


def evaluate_long_funnel(
    *,
    symbol: str,
    metrics: dict[str, Any] | None,
    blockers: list[str] | tuple[str, ...] = (),
    current_rule_score: int | None = None,
) -> LongFunnelDecision:
    normalized = tuple(str(item) for item in blockers)
    hard = tuple(item for item in normalized if item in HARD_VETO_CODES)
    soft = tuple(item for item in normalized if item not in HARD_VETO_CODES)
    setup = classify_setup(metrics)
    if setup is LongSetup.NO_VALID_SETUP and not hard:
        soft = (*soft, "NO_VALID_SETUP")
    return LongFunnelDecision(
        symbol=symbol.upper(),
        setup=setup,
        hard_vetoes=hard,
        soft_rejections=soft,
        evidence=dict(metrics or {}),
        current_rule_score=current_rule_score,
        conservative_edge=None,
        can_authorize_trade=False,
    )
