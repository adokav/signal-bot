"""Portfolio exposure limits and correlation risk (spec §24, §25).

Different coins are not automatically different risks: LONG SOL + LONG
AVAX + LONG ETH is mostly one long-crypto-beta bet. Rules applied to a
proposed new long:

- gross, net, single-asset, sector and correlated-cluster exposure caps
  (as % of equity, after adding the candidate);
- BTC-beta exposure cap; an unknown beta makes the check ``UNKNOWN``;
- an unknown pairwise correlation is treated as *correlated* — the
  conservative reading;
- daily loss, weekly loss, drawdown and position-count limits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

from trading.risk.checks import Check, CheckStatus, failed, passed, unknown


@dataclass(frozen=True)
class Position:
    symbol: str
    notional: float  # positive for long, negative for short
    sector: str
    beta_btc: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.notional) or self.notional == 0:
            raise ValueError("position notional must be finite and non-zero")


@dataclass(frozen=True)
class PortfolioLimits:
    max_gross_exposure_pct: float = 100.0
    max_net_exposure_pct: float = 100.0
    max_single_asset_pct: float = 50.0
    max_sector_pct: float = 60.0
    max_correlated_pct: float = 60.0
    max_btc_beta_exposure_pct: float = 100.0
    max_daily_loss_pct: float = 2.0
    max_weekly_loss_pct: float = 5.0
    max_drawdown_pct: float = 10.0
    max_positions: int = 3
    correlation_threshold: float = 0.7


@dataclass(frozen=True)
class PortfolioState:
    equity: float
    positions: tuple[Position, ...]
    daily_pnl_pct: float | None
    weekly_pnl_pct: float | None
    drawdown_pct: float | None


def _pct(value: float, equity: float) -> float:
    return abs(value) / equity * 100.0


def evaluate_new_position(
    state: PortfolioState,
    candidate: Position,
    *,
    correlation: Callable[[str, str], float | None],
    limits: PortfolioLimits = PortfolioLimits(),
) -> tuple[Check, ...]:
    """Checks for adding ``candidate``; each is PASS, FAIL or UNKNOWN."""

    if not math.isfinite(state.equity) or state.equity <= 0:
        return (unknown("portfolio_state", "hesap özsermayesi okunamadı"),)
    after: Sequence[Position] = (*state.positions, candidate)
    eq = state.equity
    checks: list[Check] = []

    def cap(key: str, value_pct: float, limit_pct: float, label: str) -> None:
        detail = f"{label} %{value_pct:.1f} / limit %{limit_pct:.1f}"
        checks.append(passed(key, detail) if value_pct <= limit_pct else failed(key, detail))

    cap("gross_exposure", sum(_pct(p.notional, eq) for p in after), limits.max_gross_exposure_pct, "brüt")
    cap("net_exposure", _pct(sum(p.notional for p in after), eq), limits.max_net_exposure_pct, "net")
    same_asset = sum(p.notional for p in after if p.symbol == candidate.symbol)
    cap("single_asset", _pct(same_asset, eq), limits.max_single_asset_pct, candidate.symbol)
    sector = sum(p.notional for p in after if p.sector == candidate.sector)
    cap("sector", _pct(sector, eq), limits.max_sector_pct, f"sektör {candidate.sector}")

    cluster = abs(candidate.notional)
    assumed = []
    for p in state.positions:
        if p.symbol == candidate.symbol:
            cluster += abs(p.notional)
            continue
        rho = correlation(candidate.symbol, p.symbol)
        if rho is None or not math.isfinite(rho):
            assumed.append(p.symbol)
            cluster += abs(p.notional)
        elif rho >= limits.correlation_threshold:
            cluster += abs(p.notional)
    note = f" (korelasyon bilinmiyor → korele sayıldı: {', '.join(assumed)})" if assumed else ""
    cap("correlated_exposure", _pct(cluster, eq), limits.max_correlated_pct, "korele küme" + note)

    if any(p.beta_btc is None or not math.isfinite(p.beta_btc) for p in after):
        checks.append(unknown("btc_beta", "en az bir pozisyonun BTC betası bilinmiyor"))
    else:
        beta = sum(p.notional * p.beta_btc for p in after)
        cap("btc_beta", _pct(beta, eq), limits.max_btc_beta_exposure_pct, "BTC-beta")

    count = len(after)
    checks.append(
        passed("position_count", f"{count}/{limits.max_positions}")
        if count <= limits.max_positions
        else failed("position_count", f"{count}/{limits.max_positions}")
    )
    for key, value, limit, label in (
        ("daily_loss", state.daily_pnl_pct, limits.max_daily_loss_pct, "günlük zarar"),
        ("weekly_loss", state.weekly_pnl_pct, limits.max_weekly_loss_pct, "haftalık zarar"),
        ("drawdown", state.drawdown_pct, limits.max_drawdown_pct, "drawdown"),
    ):
        if value is None or not math.isfinite(value):
            checks.append(unknown(key, f"{label} okunamadı"))
            continue
        loss = abs(min(value, 0.0)) if key != "drawdown" else abs(value)
        detail = f"{label} %{loss:.2f} / limit %{limit:.2f}"
        checks.append(passed(key, detail) if loss < limit else failed(key, detail))
    return tuple(checks)


def summarize(checks: Sequence[Check]) -> CheckStatus:
    if any(c.status is CheckStatus.FAIL for c in checks):
        return CheckStatus.FAIL
    if any(c.status is CheckStatus.UNKNOWN for c in checks):
        return CheckStatus.UNKNOWN
    return CheckStatus.PASS
