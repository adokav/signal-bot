"""Position sizing from risk, not from confidence (spec §23).

    notional = allowed portfolio risk / stop distance

where the stop distance includes round-trip costs (a stopped trade also
pays fees and slippage). The result is then capped by:

- maximum notional as a multiple of equity (leverage cap);
- liquidity: a maximum share of the instrument's 24h quote volume;
- the risk multiplier from drawdown and correlation haircuts.

Missing liquidity data sizes to zero instead of assuming depth
(AGENTS.md §2). Confidence and model scores are deliberately not inputs:
edge decides *whether* to trade (the gate), risk decides *how much*.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SizingLimits:
    risk_per_trade_pct: float = 0.5
    max_notional_multiple: float = 1.0
    max_volume_participation_pct: float = 0.1

    def __post_init__(self) -> None:
        if not 0 < self.risk_per_trade_pct <= 2.0:
            raise ValueError("risk_per_trade_pct must be within (0, 2]")
        if not 0 < self.max_notional_multiple <= 3.0:
            raise ValueError("max_notional_multiple must be within (0, 3]")
        if not 0 < self.max_volume_participation_pct <= 1.0:
            raise ValueError("volume participation must be within (0, 1]%")


@dataclass(frozen=True)
class SizingDecision:
    notional: float
    quantity: float
    risk_amount: float
    stop_distance_pct: float | None
    binding_constraint: str
    reasons: tuple[str, ...]
    can_authorize_trade: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _zero(reason: str, stop_distance_pct: float | None = None) -> SizingDecision:
    return SizingDecision(0.0, 0.0, 0.0, stop_distance_pct, "NONE", (reason,))


def position_size(
    *,
    equity: float,
    entry_price: float,
    stop_price: float,
    round_trip_cost_pct: float,
    quote_volume_24h: float | None,
    risk_multiplier: float,
    limits: SizingLimits = SizingLimits(),
) -> SizingDecision:
    """Long-only sizing; returns a zero-size decision whenever an input is unusable."""

    for value in (equity, entry_price, stop_price, round_trip_cost_pct, risk_multiplier):
        if not math.isfinite(value):
            return _zero("NON_FINITE_INPUT")
    if equity <= 0:
        return _zero("NO_EQUITY")
    if entry_price <= 0 or stop_price <= 0 or stop_price >= entry_price:
        return _zero("INVALID_STOP_GEOMETRY")
    if round_trip_cost_pct < 0:
        return _zero("NEGATIVE_COST")
    if not 0 <= risk_multiplier <= 1:
        return _zero("INVALID_RISK_MULTIPLIER")
    stop_distance_pct = (entry_price - stop_price) / entry_price * 100.0 + round_trip_cost_pct
    if risk_multiplier == 0:
        return _zero("RISK_MULTIPLIER_ZERO", stop_distance_pct)
    if quote_volume_24h is None or not math.isfinite(quote_volume_24h) or quote_volume_24h <= 0:
        return _zero("LIQUIDITY_UNKNOWN", stop_distance_pct)

    risk_budget = equity * limits.risk_per_trade_pct / 100.0 * risk_multiplier
    candidates = {
        "RISK_BUDGET": risk_budget / (stop_distance_pct / 100.0),
        "LEVERAGE_CAP": equity * limits.max_notional_multiple,
        "LIQUIDITY_CAP": quote_volume_24h * limits.max_volume_participation_pct / 100.0,
    }
    binding = min(candidates, key=candidates.get)
    notional = candidates[binding]
    return SizingDecision(
        notional=notional,
        quantity=notional / entry_price,
        risk_amount=notional * stop_distance_pct / 100.0,
        stop_distance_pct=stop_distance_pct,
        binding_constraint=binding,
        reasons=(),
    )
