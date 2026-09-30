"""Expected value after every cost (spec §12).

    EV = P(win) · avg_win − P(loss) · avg_loss
         − fee − spread − slippage − funding − execution

All quantities are percentage points of notional. A setup is only worth
considering when:

1. the win probability is **calibrated** (spec §11) — a raw model score is
   never accepted here;
2. net EV clears an explicit minimum margin (tiny positive EV is noise);
3. net EV is still positive at the *lower* confidence bound of the win
   probability, so sampling error alone cannot flip the sign.

The result is evidence for a gate, not trade permission.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


DEFAULT_MIN_NET_EV_PCT = 0.10


@dataclass(frozen=True)
class CostEstimate:
    fee_pct: float
    spread_pct: float
    slippage_pct: float
    funding_pct: float
    execution_pct: float = 0.0

    def __post_init__(self) -> None:
        for name in ("fee_pct", "spread_pct", "slippage_pct", "execution_pct"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not math.isfinite(self.funding_pct):
            raise ValueError("funding_pct must be finite")

    @property
    def total_pct(self) -> float:
        return (
            self.fee_pct
            + self.spread_pct
            + self.slippage_pct
            + self.funding_pct
            + self.execution_pct
        )


@dataclass(frozen=True)
class ExpectedValue:
    p_win: float
    p_win_lower: float | None
    avg_win_pct: float
    avg_loss_pct: float
    gross_ev_pct: float
    cost_pct: float
    net_ev_pct: float
    net_ev_at_lower_pct: float | None
    breakeven_probability: float
    min_net_ev_pct: float
    passes: bool
    reasons: tuple[str, ...]
    can_authorize_trade: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def expected_value(
    *,
    p_win: float,
    avg_win_pct: float,
    avg_loss_pct: float,
    costs: CostEstimate,
    probability_is_calibrated: bool,
    p_win_lower: float | None = None,
    min_net_ev_pct: float = DEFAULT_MIN_NET_EV_PCT,
) -> ExpectedValue:
    """``avg_loss_pct`` is a positive magnitude (1.2 means a 1.2% loss)."""

    for name, value in (("p_win", p_win), ("avg_win_pct", avg_win_pct), ("avg_loss_pct", avg_loss_pct)):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if not 0.0 <= p_win <= 1.0:
        raise ValueError("p_win must be within [0, 1]")
    if p_win_lower is not None and not 0.0 <= p_win_lower <= p_win:
        raise ValueError("p_win_lower must be within [0, p_win]")
    if avg_win_pct <= 0 or avg_loss_pct <= 0:
        raise ValueError("average win and loss magnitudes must be positive")
    if min_net_ev_pct < 0:
        raise ValueError("min_net_ev_pct cannot be negative")

    def _net(p: float) -> float:
        return p * avg_win_pct - (1.0 - p) * avg_loss_pct - costs.total_pct

    gross = p_win * avg_win_pct - (1.0 - p_win) * avg_loss_pct
    net = gross - costs.total_pct
    net_lower = _net(p_win_lower) if p_win_lower is not None else None
    breakeven = (avg_loss_pct + costs.total_pct) / (avg_win_pct + avg_loss_pct)
    reasons: list[str] = []
    if not probability_is_calibrated:
        reasons.append("UNCALIBRATED_PROBABILITY")
    if net <= 0:
        reasons.append("NEGATIVE_NET_EV")
    elif net < min_net_ev_pct:
        reasons.append("NET_EV_BELOW_SAFETY_MARGIN")
    if net_lower is None:
        reasons.append("PROBABILITY_UNCERTAINTY_UNKNOWN")
    elif net_lower <= 0:
        reasons.append("NET_EV_NOT_POSITIVE_AT_LOWER_BOUND")
    return ExpectedValue(
        p_win=p_win,
        p_win_lower=p_win_lower,
        avg_win_pct=avg_win_pct,
        avg_loss_pct=avg_loss_pct,
        gross_ev_pct=gross,
        cost_pct=costs.total_pct,
        net_ev_pct=net,
        net_ev_at_lower_pct=net_lower,
        breakeven_probability=breakeven,
        min_net_ev_pct=min_net_ev_pct,
        passes=not reasons,
        reasons=tuple(reasons),
    )
