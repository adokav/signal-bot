"""Kill switch evaluator and latch (spec §30).

Deterministic and independent of any AI model. It trips on stale market
data, exchange/bot state mismatch, abnormal spread, excessive slippage,
duplicate orders, loss/drawdown limits, repeated execution failures,
untested extreme volatility or a broken data source. A required input
that cannot be read trips it as well (unknown is not safe).

Once tripped the latch stays tripped until an operator explicitly resets
it with an acknowledgement *and* every condition is clear again. The
evaluator only describes the required actions; there is no execution
engine in this repository to carry them out yet (AGENTS.md §10).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field


REQUIRED_ACTIONS = (
    "STOP_NEW_ORDERS",
    "CANCEL_RISKY_PENDING_ORDERS",
    "KEEP_PROTECTIVE_ORDERS",
    "NOTIFY_OPERATOR",
    "LOG_REASON",
    "REQUIRE_EXPLICIT_RESTART_APPROVAL",
)


@dataclass(frozen=True)
class KillSwitchLimits:
    max_data_age_seconds: int = 120
    max_spread_bps: float = 15.0
    max_slippage_bps: float = 25.0
    max_daily_loss_pct: float = 2.0
    max_drawdown_pct: float = 10.0
    max_execution_failures: int = 3
    max_tested_volatility_percentile: float = 0.99


@dataclass(frozen=True)
class KillSwitchInputs:
    now: int
    last_market_data_at: int | None
    data_source_healthy: bool | None
    exchange_state_matches: bool | None
    spread_bps: float | None
    recent_slippage_bps: float | None
    duplicate_order_detected: bool | None
    daily_pnl_pct: float | None
    drawdown_pct: float | None
    recent_execution_failures: int | None
    volatility_percentile: float | None


def _bad(value: float | None) -> bool:
    return value is None or not math.isfinite(value)


def kill_switch_reasons(
    inputs: KillSwitchInputs, limits: KillSwitchLimits = KillSwitchLimits()
) -> tuple[str, ...]:
    reasons: list[str] = []
    if inputs.last_market_data_at is None:
        reasons.append("MARKET_DATA_UNKNOWN")
    elif inputs.now - inputs.last_market_data_at > limits.max_data_age_seconds:
        reasons.append("STALE_MARKET_DATA")
    if inputs.data_source_healthy is not True:
        reasons.append("DATA_SOURCE_UNHEALTHY" if inputs.data_source_healthy is False else "DATA_SOURCE_UNKNOWN")
    if inputs.exchange_state_matches is not True:
        reasons.append("EXCHANGE_STATE_MISMATCH" if inputs.exchange_state_matches is False else "EXCHANGE_STATE_UNKNOWN")
    if _bad(inputs.spread_bps):
        reasons.append("SPREAD_UNKNOWN")
    elif inputs.spread_bps > limits.max_spread_bps:
        reasons.append("ABNORMAL_SPREAD")
    if _bad(inputs.recent_slippage_bps):
        reasons.append("SLIPPAGE_UNKNOWN")
    elif inputs.recent_slippage_bps > limits.max_slippage_bps:
        reasons.append("EXCESSIVE_SLIPPAGE")
    if inputs.duplicate_order_detected is not False:
        reasons.append("DUPLICATE_ORDER" if inputs.duplicate_order_detected else "DUPLICATE_CHECK_UNKNOWN")
    if _bad(inputs.daily_pnl_pct):
        reasons.append("DAILY_PNL_UNKNOWN")
    elif inputs.daily_pnl_pct <= -limits.max_daily_loss_pct:
        reasons.append("DAILY_LOSS_LIMIT")
    if _bad(inputs.drawdown_pct):
        reasons.append("DRAWDOWN_UNKNOWN")
    elif abs(inputs.drawdown_pct) >= limits.max_drawdown_pct:
        reasons.append("DRAWDOWN_LIMIT")
    if inputs.recent_execution_failures is None:
        reasons.append("EXECUTION_HEALTH_UNKNOWN")
    elif inputs.recent_execution_failures >= limits.max_execution_failures:
        reasons.append("REPEATED_EXECUTION_FAILURES")
    if _bad(inputs.volatility_percentile):
        reasons.append("VOLATILITY_UNKNOWN")
    elif inputs.volatility_percentile > limits.max_tested_volatility_percentile:
        reasons.append("UNTESTED_EXTREME_VOLATILITY")
    return tuple(reasons)


@dataclass
class KillSwitchLatch:
    tripped: bool = False
    tripped_at: int | None = None
    reasons: tuple[str, ...] = ()
    history: list[dict] = field(default_factory=list)

    def evaluate(self, inputs: KillSwitchInputs, limits: KillSwitchLimits = KillSwitchLimits()) -> tuple[str, ...]:
        """Returns the actions required now (empty when armed and clear)."""

        reasons = kill_switch_reasons(inputs, limits)
        if reasons and not self.tripped:
            self.tripped = True
            self.tripped_at = inputs.now
            self.reasons = reasons
            self.history.append({"event": "TRIPPED", "at": inputs.now, "reasons": list(reasons)})
        return REQUIRED_ACTIONS if self.tripped else ()

    def reset(
        self,
        *,
        operator_ack: str,
        inputs: KillSwitchInputs,
        limits: KillSwitchLimits = KillSwitchLimits(),
    ) -> bool:
        """Clear the latch only with an explicit acknowledgement and clear conditions."""

        if not self.tripped:
            return True
        if not operator_ack or not operator_ack.strip():
            return False
        if kill_switch_reasons(inputs, limits):
            return False
        self.history.append({"event": "RESET", "at": inputs.now, "ack": operator_ack.strip()[:200]})
        self.tripped = False
        self.tripped_at = None
        self.reasons = ()
        return True

    def to_dict(self) -> dict:
        return asdict(self)
