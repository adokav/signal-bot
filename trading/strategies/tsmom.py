"""Time-series momentum + volatility targeting hypothesis.

Reference: Moskowitz, Ooi & Pedersen 2012 ("Time Series Momentum"), and the
kripto-adapted variant popularized by Liu & Tsyvinski 2018. The signal:

- **Direction.** Take the sign of the trailing ``lookback_days`` log-return
  on daily bars. Long only for this project (rules elsewhere restrict the
  bot to long-weighted futures); negative sign becomes ``NO_TRADE``.
- **Position sizing.** Scale exposure by ``target_annualized_vol /
  realized_annualized_vol``, capped at ``max_leverage``. This is the AQR
  volatility-targeting overlay: same signal, but riskier assets get
  smaller notional so the *risk contribution* is stable across bars.
- **Exit contract.** Emit an ``ExitPlan`` for the harness: T1 partial exit
  at +ATR_1x, stop-to-entry after T1, T2 at +ATR_2x. Time-stop at 48h. The
  harness applies these against realized OHLC.

The strategy is a pure function of a daily candle series and does not
authorize trades (AGENTS.md §4). It emits deterministic ``TsmomDecision``
records the backtest harness turns into cost-adjusted PnL.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from trading.data.binance_perp import Candle


TRADING_DAYS_PER_YEAR = 365  # crypto trades weekends


class TsmomSignal(str, Enum):
    LONG = "LONG"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class ExitPlan:
    """Structural exit contract paired with each entry."""

    entry_price: float
    hard_stop: float
    target_1: float
    target_2: float
    time_stop_seconds: int

    def __post_init__(self) -> None:
        if self.entry_price <= 0:
            raise ValueError("entry_price must be positive")
        if not self.hard_stop < self.entry_price:
            raise ValueError("hard_stop must be below entry for a long")
        if not self.entry_price < self.target_1 <= self.target_2:
            raise ValueError("target geometry inconsistent")
        if self.time_stop_seconds <= 0:
            raise ValueError("time_stop_seconds must be positive")


@dataclass(frozen=True)
class TsmomDecision:
    """Deterministic per-bar output of the strategy."""

    decision_at: int
    symbol: str
    signal: TsmomSignal
    lookback_return_pct: float
    realized_annualized_vol_pct: float
    position_scale: float
    entry_price: float | None
    plan: ExitPlan | None
    reasons: tuple[str, ...] = ()
    can_authorize_trade: bool = False

    def __post_init__(self) -> None:
        if self.can_authorize_trade:
            raise ValueError("research strategy cannot authorize a trade")
        if self.signal is TsmomSignal.LONG and (self.plan is None or self.entry_price is None):
            raise ValueError("LONG signal requires entry_price and plan")


@dataclass(frozen=True)
class TsmomParams:
    lookback_days: int = 60
    realized_vol_lookback_days: int = 30
    target_annualized_vol_pct: float = 40.0
    # Cap raised the strategy's max drawdown to -122% in the first Faz A run
    # (see docs/BACKTEST_REPORT_v1.md history). Effective 1x notional now:
    # vol targeting still scales positions down when realized vol is high,
    # but never above unit leverage. Real cost drag drops with fewer forced
    # exits, and cumulative equity math cannot compound past -100%.
    max_leverage: float = 1.0
    atr_lookback_days: int = 14
    # 48 h expiry cycled trades faster than the trend it was trying to catch
    # (578 trades / 3 years). One week keeps at least one full swing per
    # trade and roughly halves the round-trip cost drag.
    time_stop_seconds: int = 7 * 24 * 3600
    target_1_atr_mult: float = 1.0
    target_2_atr_mult: float = 2.0
    stop_atr_mult: float = 1.5


def _log_returns(closes: Sequence[float]) -> list[float]:
    if len(closes) < 2:
        return []
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]


def realized_annualized_vol_pct(closes: Sequence[float], *, lookback: int) -> float:
    """Realized annualized volatility from the last ``lookback`` daily returns.

    Uses log-returns; annualization factor sqrt(365) since crypto trades
    every day.
    """

    if lookback <= 1:
        raise ValueError("vol lookback must be > 1")
    if len(closes) < lookback + 1:
        raise ValueError("not enough closes for vol lookback")
    rets = _log_returns(closes[-(lookback + 1):])
    mean = sum(rets) / len(rets)
    variance = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    stdev = math.sqrt(variance)
    return stdev * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0


def lookback_return_pct(closes: Sequence[float], *, lookback: int) -> float:
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    if len(closes) < lookback + 1:
        raise ValueError("not enough closes for return lookback")
    return (closes[-1] / closes[-1 - lookback] - 1.0) * 100.0


def atr_pct(candles: Sequence[Candle], *, lookback: int) -> float:
    """Average true range as a fraction of last close, in percentage points."""

    if lookback < 1:
        raise ValueError("ATR lookback must be >= 1")
    if len(candles) < lookback + 1:
        raise ValueError("not enough candles for ATR")
    trs: list[float] = []
    for i in range(1, len(candles)):
        prev_close = candles[i - 1].close
        current = candles[i]
        tr = max(
            current.high - current.low,
            abs(current.high - prev_close),
            abs(current.low - prev_close),
        )
        trs.append(tr)
    avg_tr = sum(trs[-lookback:]) / lookback
    return avg_tr / candles[-1].close * 100.0


def position_scale(
    *,
    realized_vol_pct: float,
    target_vol_pct: float,
    max_leverage: float,
) -> float:
    """Volatility-targeting scale factor.

    Returns a positive number in ``[0, max_leverage]``. When realized vol
    is zero (degenerate constant series) returns 0 rather than infinity —
    fail closed rather than assume unlimited edge.
    """

    if target_vol_pct <= 0 or max_leverage <= 0:
        raise ValueError("target_vol_pct and max_leverage must be positive")
    if realized_vol_pct <= 0:
        return 0.0
    raw = target_vol_pct / realized_vol_pct
    return max(0.0, min(max_leverage, raw))


def evaluate_tsmom(
    candles: Sequence[Candle],
    *,
    symbol: str,
    decision_at: int,
    params: TsmomParams = TsmomParams(),
) -> TsmomDecision:
    """Return the TSMOM decision for the last visible daily bar.

    The strategy consumes DAILY candles ordered chronologically. It uses only
    closed candles by construction: the caller must have already filtered
    open candles at the data-adapter boundary (`parse_klines` enforces this).
    """

    needed = max(
        params.lookback_days,
        params.realized_vol_lookback_days,
        params.atr_lookback_days,
    ) + 1
    if len(candles) < needed:
        return TsmomDecision(
            decision_at=decision_at,
            symbol=symbol.upper(),
            signal=TsmomSignal.NO_TRADE,
            lookback_return_pct=0.0,
            realized_annualized_vol_pct=0.0,
            position_scale=0.0,
            entry_price=None,
            plan=None,
            reasons=("INSUFFICIENT_HISTORY",),
        )
    closes = [candle.close for candle in candles]
    trailing_return = lookback_return_pct(closes, lookback=params.lookback_days)
    realized_vol = realized_annualized_vol_pct(
        closes, lookback=params.realized_vol_lookback_days
    )
    scale = position_scale(
        realized_vol_pct=realized_vol,
        target_vol_pct=params.target_annualized_vol_pct,
        max_leverage=params.max_leverage,
    )
    if trailing_return <= 0:
        return TsmomDecision(
            decision_at=decision_at,
            symbol=symbol.upper(),
            signal=TsmomSignal.NO_TRADE,
            lookback_return_pct=trailing_return,
            realized_annualized_vol_pct=realized_vol,
            position_scale=0.0,
            entry_price=None,
            plan=None,
            reasons=("MOMENTUM_NEGATIVE_OR_FLAT",),
        )
    if scale <= 0:
        return TsmomDecision(
            decision_at=decision_at,
            symbol=symbol.upper(),
            signal=TsmomSignal.NO_TRADE,
            lookback_return_pct=trailing_return,
            realized_annualized_vol_pct=realized_vol,
            position_scale=0.0,
            entry_price=None,
            plan=None,
            reasons=("VOL_SCALE_ZERO",),
        )
    atr = atr_pct(candles, lookback=params.atr_lookback_days)
    entry = candles[-1].close
    plan = ExitPlan(
        entry_price=entry,
        hard_stop=entry * (1 - params.stop_atr_mult * atr / 100.0),
        target_1=entry * (1 + params.target_1_atr_mult * atr / 100.0),
        target_2=entry * (1 + params.target_2_atr_mult * atr / 100.0),
        time_stop_seconds=params.time_stop_seconds,
    )
    return TsmomDecision(
        decision_at=decision_at,
        symbol=symbol.upper(),
        signal=TsmomSignal.LONG,
        lookback_return_pct=trailing_return,
        realized_annualized_vol_pct=realized_vol,
        position_scale=scale,
        entry_price=entry,
        plan=plan,
        reasons=(
            f"MOMENTUM_POSITIVE_{params.lookback_days}D",
            f"VOL_SCALE_{scale:.2f}X",
            f"ATR_{atr:.2f}PCT",
        ),
    )
