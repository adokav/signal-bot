"""Drawdown-scaled risk (spec §26).

Risk falls as drawdown deepens and new trades stop at the halt level:

    0-3%  → 100% of normal risk      5-8%  → 50%
    3-5%  → 75%                      8-10% → 25%
    ≥10%  → 0 (halt new trades)

These levels are the spec's starting point, not fitted values; the final
thresholds must come from backtest and risk analysis. Martingale, doubling
down or loss chasing is structurally impossible here: the multiplier never
exceeds 1.0 and never grows with losses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


DEFAULT_LADDER: tuple[tuple[float, float], ...] = (
    (3.0, 1.0),
    (5.0, 0.75),
    (8.0, 0.5),
    (10.0, 0.25),
)
DEFAULT_HALT_PCT = 10.0


@dataclass(frozen=True)
class DrawdownRisk:
    drawdown_pct: float | None
    multiplier: float
    state: str  # NORMAL | REDUCED | HALTED | UNKNOWN


def current_drawdown_pct(equity_curve: Sequence[float]) -> float:
    """Current decline from the running peak, as a positive percentage."""

    if not equity_curve:
        raise ValueError("equity curve is empty")
    peak = -math.inf
    for value in equity_curve:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("equity values must be finite and positive")
        peak = max(peak, value)
    return (1.0 - equity_curve[-1] / peak) * 100.0


def drawdown_risk_multiplier(
    drawdown_pct: float | None,
    *,
    ladder: Sequence[tuple[float, float]] = DEFAULT_LADDER,
    halt_pct: float = DEFAULT_HALT_PCT,
) -> DrawdownRisk:
    """Map a drawdown magnitude (sign ignored) to a risk multiplier.

    An unknown or non-finite drawdown halts: if the account state cannot
    be read, the risk engine must not assume it is healthy.
    """

    thresholds = [t for t, _ in ladder]
    multipliers = [m for _, m in ladder]
    if thresholds != sorted(thresholds) or any(not 0 <= m <= 1 for m in multipliers):
        raise ValueError("ladder must be increasing with multipliers in [0, 1]")
    if multipliers != sorted(multipliers, reverse=True):
        raise ValueError("risk multiplier must not grow with drawdown")
    if drawdown_pct is None or not math.isfinite(drawdown_pct):
        return DrawdownRisk(None, 0.0, "UNKNOWN")
    dd = abs(drawdown_pct)
    if dd >= halt_pct:
        return DrawdownRisk(dd, 0.0, "HALTED")
    for threshold, multiplier in ladder:
        if dd < threshold:
            return DrawdownRisk(dd, multiplier, "NORMAL" if multiplier >= 1.0 else "REDUCED")
    return DrawdownRisk(dd, 0.0, "HALTED")
