"""Pre-live promotion checklist (spec §33, §41).

A strategy may move toward live capital only after every check passes.
Backtest evidence can fill the first group; paper trading, small live
deployment and a kill-switch drill can only be filled by running them, so
a backtest alone can never make a strategy promotable. ``NOT_RUN`` is
never treated as a pass (AGENTS.md §2).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


PASS = "PASS"
FAIL = "FAIL"
NOT_RUN = "NOT_RUN"
NOT_APPLICABLE = "NOT_APPLICABLE"
STATES = {PASS, FAIL, NOT_RUN, NOT_APPLICABLE}

BACKTEST_CHECKS = (
    "out_of_sample",
    "walk_forward",
    "transaction_cost_stress",
    "entry_delay_stress",
    "parameter_perturbation",
    "bootstrap_expectancy",
    "monte_carlo_drawdown",
    "deflated_sharpe",
    "probability_calibration",
)
LIVE_CHECKS = ("paper_trading", "small_live_deployment", "kill_switch_test")


@dataclass(frozen=True)
class PromotionCheck:
    name: str
    state: str
    detail: str

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"unknown check state {self.state!r}")


@dataclass(frozen=True)
class PromotionReadiness:
    checks: tuple[PromotionCheck, ...]
    stage: str
    promotable_to_live: bool
    can_authorize_trade: bool = False

    def to_dict(self) -> dict:
        return {
            "checks": [asdict(c) for c in self.checks],
            "stage": self.stage,
            "promotable_to_live": self.promotable_to_live,
            "can_authorize_trade": False,
        }


def assess_promotion(backtest_checks: dict[str, PromotionCheck]) -> PromotionReadiness:
    missing = [name for name in BACKTEST_CHECKS if name not in backtest_checks]
    unknown = [name for name in backtest_checks if name not in BACKTEST_CHECKS]
    if missing or unknown:
        raise ValueError(f"checklist mismatch: missing={missing} unknown={unknown}")
    checks = [backtest_checks[name] for name in BACKTEST_CHECKS]
    checks += [
        PromotionCheck(name, NOT_RUN, "yalnızca gerçek çalıştırmayla doldurulabilir")
        for name in LIVE_CHECKS
    ]
    backtest = checks[: len(BACKTEST_CHECKS)]
    if any(c.state == FAIL for c in backtest):
        stage = "REJECTED_AT_BACKTEST"
    elif any(c.state == NOT_RUN for c in backtest):
        stage = "INCOMPLETE_EVIDENCE"
    else:
        stage = "BACKTEST_PASSED_PAPER_REQUIRED"
    promotable = all(c.state in {PASS, NOT_APPLICABLE} for c in checks)
    return PromotionReadiness(checks=tuple(checks), stage=stage, promotable_to_live=promotable)
