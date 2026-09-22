"""Cost model for Binance USDⓈ-M perpetual long-only research.

Turns a hypothetical trade into a realistic round-trip cost expressed as a
percentage of notional. Three additive components:

1. **Taker fees** — entry and exit taker fees (VIP 0 default: 4bps each way,
   configurable). The strategy contract assumes market/aggressor exits, so
   both legs are taker.
2. **Funding accrual** — long positions pay short positions whenever
   funding is positive at an interval boundary crossed during the holding
   period. For a hold that spans N funding accruals the total cost is the
   sum of those N rates, applied to notional.
3. **Slippage** — per-side impact estimate. Order-book depth is not
   available at backtest time from historical parquet, so a conservative
   default is used (2bps per side ≈ 4bps round-trip). The function accepts
   an override for callers that plumb in a live depth snapshot.

The returned ``cost_pct`` is what the backtest harness subtracts from the
gross return before computing Sharpe / expectancy / R-multiples. It has no
authority to place, size, or cancel an order (AGENTS.md §4, §10).

The math is deliberately explicit and side-effect free so callers can unit
test each component independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from trading.data.binance_perp import FundingRow


DEFAULT_TAKER_FEE_BPS = 4.0  # 0.04% per side (VIP 0 USDⓈ-M perp)
DEFAULT_SLIPPAGE_BPS = 2.0  # 0.02% per side conservative default


@dataclass(frozen=True)
class CostBreakdown:
    """A round-trip cost decomposition, in percentage points of notional."""

    taker_fee_pct: float
    funding_pct: float
    slippage_pct: float

    @property
    def total_pct(self) -> float:
        return self.taker_fee_pct + self.funding_pct + self.slippage_pct

    def as_dict(self) -> dict[str, float]:
        return {
            "taker_fee_pct": self.taker_fee_pct,
            "funding_pct": self.funding_pct,
            "slippage_pct": self.slippage_pct,
            "total_pct": self.total_pct,
        }


def taker_fee_round_trip_pct(*, taker_fee_bps: float = DEFAULT_TAKER_FEE_BPS) -> float:
    if taker_fee_bps < 0:
        raise ValueError("taker_fee_bps cannot be negative")
    return 2.0 * taker_fee_bps / 100.0


def slippage_round_trip_pct(*, slippage_bps: float = DEFAULT_SLIPPAGE_BPS) -> float:
    if slippage_bps < 0:
        raise ValueError("slippage_bps cannot be negative")
    return 2.0 * slippage_bps / 100.0


def accrued_funding_pct(
    funding_history: Iterable[FundingRow],
    *,
    entry_time: int,
    exit_time: int,
) -> float:
    """Return the accrued funding cost for a long across [entry_time, exit_time].

    Long positions pay funding at each interval boundary strictly greater
    than ``entry_time`` and less than or equal to ``exit_time``. A positive
    funding rate is a cost (positive return here means negative PnL for the
    long), a negative rate is a rebate. Sum expressed as percentage points.

    Example: three intervals accrue rates 0.0001, 0.0002, -0.00005 during
    the hold → total ``pct = 100 * (0.0001 + 0.0002 - 0.00005) = 0.025``.
    """

    if entry_time < 0 or exit_time <= entry_time:
        raise ValueError("invalid holding interval")
    rows = sorted(funding_history, key=lambda item: item.funding_time)
    accrued = 0.0
    for row in rows:
        if entry_time < row.funding_time <= exit_time:
            accrued += row.funding_rate
    return accrued * 100.0


def round_trip_cost_pct(
    *,
    entry_time: int,
    exit_time: int,
    funding_history: Sequence[FundingRow] | None = None,
    taker_fee_bps: float = DEFAULT_TAKER_FEE_BPS,
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
) -> CostBreakdown:
    """Compose the three components into a single ``CostBreakdown``.

    ``funding_history`` may be omitted when the caller only needs the
    fee+slippage baseline (e.g. for equity backtests without a funding
    stream); the funding component then contributes zero. That is a
    deliberate cost *underestimate* and callers should mark such results
    explicitly.
    """

    fee_pct = taker_fee_round_trip_pct(taker_fee_bps=taker_fee_bps)
    slip_pct = slippage_round_trip_pct(slippage_bps=slippage_bps)
    funding_pct = 0.0
    if funding_history is not None:
        funding_pct = accrued_funding_pct(
            funding_history,
            entry_time=entry_time,
            exit_time=exit_time,
        )
    return CostBreakdown(
        taker_fee_pct=fee_pct,
        funding_pct=funding_pct,
        slippage_pct=slip_pct,
    )
