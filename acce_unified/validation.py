"""Dependency-free out-of-sample promotion gate inspired by mm_trading.

The useful part of mm_trading is not its synthetic strategy; it is the rule
that a signal observed at t may only affect t+1 and must survive costs and an
out-of-sample window. This module applies that discipline to realised R values.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import fmean


@dataclass(frozen=True)
class ValidationPolicy:
    min_total_trades: int = 40
    min_oos_trades: int = 16
    oos_fraction: float = 0.40
    min_profit_factor: float = 1.20
    min_expectancy_r: float = 0.05
    max_drawdown_r: float = 6.0


def _wilson_lower_bound(wins: int, total: int, z: float = 1.96) -> float | None:
    if total <= 0:
        return None
    p = wins / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - spread) / denom)


def summarize_r_multiples(values: list[float]) -> dict:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "win_rate_wilson_low": None,
            "profit_factor": None,
            "expectancy_r": None,
            "max_drawdown_r": 0.0,
        }
    wins = [value for value in clean if value > 0]
    losses = [value for value in clean if value < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else None)
    equity = peak = 0.0
    max_drawdown = 0.0
    for value in clean:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return {
        "trades": len(clean),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(clean),
        "win_rate_wilson_low": _wilson_lower_bound(len(wins), len(clean)),
        "profit_factor": profit_factor,
        "expectancy_r": fmean(clean),
        "avg_win_r": fmean(wins) if wins else None,
        "avg_loss_r": fmean(losses) if losses else None,
        "max_drawdown_r": max_drawdown,
    }


def evaluate_promotion(values: list[float], policy: ValidationPolicy | None = None) -> dict:
    policy = policy or ValidationPolicy()
    total = len(values)
    oos_count = max(policy.min_oos_trades, int(math.ceil(total * policy.oos_fraction)))
    oos = values[-oos_count:] if total >= oos_count else list(values)
    all_metrics = summarize_r_multiples(values)
    oos_metrics = summarize_r_multiples(oos)
    reasons: list[str] = []
    if total < policy.min_total_trades:
        reasons.append(f"örneklem {total}/{policy.min_total_trades}")
    if int(oos_metrics["trades"]) < policy.min_oos_trades:
        reasons.append(f"OOS örneklem {oos_metrics['trades']}/{policy.min_oos_trades}")
    pf = oos_metrics.get("profit_factor")
    if pf is None or pf < policy.min_profit_factor:
        reasons.append(f"OOS profit factor {pf if pf is not None else 'N/A'} < {policy.min_profit_factor}")
    expectancy = oos_metrics.get("expectancy_r")
    if expectancy is None or expectancy < policy.min_expectancy_r:
        reasons.append(f"OOS expectancy {expectancy if expectancy is not None else 'N/A'} < {policy.min_expectancy_r}R")
    if float(oos_metrics.get("max_drawdown_r") or 0.0) > policy.max_drawdown_r:
        reasons.append(f"OOS drawdown > {policy.max_drawdown_r}R")
    return {
        "passed": not reasons,
        "decision": "PROMOTE_TO_ADVISORY" if not reasons else "KEEP_SHADOW",
        "reasons": reasons,
        "policy": asdict(policy),
        "all": all_metrics,
        "out_of_sample": oos_metrics,
    }
