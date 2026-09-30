"""Final trade gate and candidate status (spec §3, §38, §40).

Twelve questions, each answered PASS / FAIL / UNKNOWN. A missing answer is
UNKNOWN; UNKNOWN is never a pass. Any hard check that is not PASS means
NO TRADE.

Status classification — a high model confidence alone can never lift a
candidate above WATCH:

- ``REJECT`` — a setup check fails (stale data, illiquid, stop not at the
  invalidation level, regime incompatible) or evidence says the edge is
  negative (out-of-sample expectancy or net EV fails).
- ``WATCH`` — nothing disqualifying, but the setup or its evidence is
  incomplete (uncalibrated, untested parameters, thin EV margin, unknown).
- ``QUALIFIED`` — setup and evidence all pass; portfolio, drawdown or
  execution readiness do not.
- ``EXECUTION_READY`` — all twelve pass. Even then the decision carries
  ``can_authorize_trade = False``: authorization belongs to a separately
  reviewed execution path (AGENTS.md §4, §10), which does not exist here.

The three §3 questions are derived from the same answers: observable edge
(setup), historically validated (out-of-sample + calibrated + tested
parameters), sufficient reward for the risk (net EV + margin).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from trading.risk.checks import Check, CheckStatus


GATE_QUESTIONS: dict[str, str] = {
    "data_fresh": "Veri güncel mi?",
    "liquid_enough": "Coin yeterince likit mi?",
    "regime_compatible": "Piyasa rejimi stratejiyle uyumlu mu?",
    "oos_expectancy_positive": "Benzer setup'ların out-of-sample expectancy'si pozitif mi?",
    "parameters_in_tested_zone": "Sinyal test edilmiş parametre bölgesinde mi?",
    "probabilities_calibrated": "Olasılıklar kalibre edilmiş mi?",
    "net_ev_positive": "Bütün maliyetlerden sonra EV pozitif mi?",
    "ev_margin_sufficient": "EV yeterli güvenlik marjına sahip mi?",
    "correlation_acceptable": "Portföy korelasyonu kabul edilebilir mi?",
    "drawdown_within_limits": "Drawdown risk limitleri içinde mi?",
    "execution_healthy": "Execution sistemi sağlıklı mı?",
    "stop_at_invalidation": "Stop gerçekten setup invalidation seviyesine bağlı mı?",
}

SETUP_CHECKS = ("data_fresh", "liquid_enough", "regime_compatible", "stop_at_invalidation")
EVIDENCE_CHECKS = (
    "oos_expectancy_positive",
    "parameters_in_tested_zone",
    "probabilities_calibrated",
    "net_ev_positive",
    "ev_margin_sufficient",
)
READINESS_CHECKS = ("correlation_acceptable", "drawdown_within_limits", "execution_healthy")
DISQUALIFYING_EVIDENCE = ("oos_expectancy_positive", "net_ev_positive")

STATUSES = ("REJECT", "WATCH", "QUALIFIED", "EXECUTION_READY")


@dataclass(frozen=True)
class GateDecision:
    symbol: str
    status: str
    no_trade: bool
    checks: tuple[Check, ...]
    reasons: tuple[str, ...]
    edge_observable: CheckStatus
    edge_validated: CheckStatus
    reward_sufficient: CheckStatus
    can_authorize_trade: bool = False

    def __post_init__(self) -> None:
        if self.can_authorize_trade:
            raise ValueError("the gate cannot authorize a trade")
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}")

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "status": self.status,
            "no_trade": self.no_trade,
            "checks": [c.to_dict() for c in self.checks],
            "reasons": list(self.reasons),
            "edge_observable": self.edge_observable.value,
            "edge_validated": self.edge_validated.value,
            "reward_sufficient": self.reward_sufficient.value,
            "can_authorize_trade": False,
        }


def _combine(statuses: list[CheckStatus]) -> CheckStatus:
    if any(s is CheckStatus.FAIL for s in statuses):
        return CheckStatus.FAIL
    if any(s is CheckStatus.UNKNOWN for s in statuses):
        return CheckStatus.UNKNOWN
    return CheckStatus.PASS


def evaluate_gate(symbol: str, answers: Mapping[str, Check]) -> GateDecision:
    unknown_keys = [k for k in answers if k not in GATE_QUESTIONS]
    if unknown_keys:
        raise ValueError(f"unknown gate checks: {unknown_keys}")
    for key, check in answers.items():
        if check.key != key:
            raise ValueError(f"check {check.key!r} supplied as answer to {key!r}")
    checks = tuple(
        answers.get(key) or Check(key, CheckStatus.UNKNOWN, "değerlendirilmedi")
        for key in GATE_QUESTIONS
    )
    by_key = {c.key: c for c in checks}

    def status_of(keys) -> list[CheckStatus]:
        return [by_key[k].status for k in keys]

    reasons = tuple(
        f"{c.key}:{c.status.value}" for c in checks if c.status is not CheckStatus.PASS
    )
    setup = _combine(status_of(SETUP_CHECKS))
    evidence = _combine(status_of(EVIDENCE_CHECKS))
    readiness = _combine(status_of(READINESS_CHECKS))
    disqualified = any(by_key[k].status is CheckStatus.FAIL for k in DISQUALIFYING_EVIDENCE)

    if setup is CheckStatus.FAIL or disqualified:
        status = "REJECT"
    elif setup is not CheckStatus.PASS or evidence is not CheckStatus.PASS:
        status = "WATCH"
    elif readiness is not CheckStatus.PASS:
        status = "QUALIFIED"
    else:
        status = "EXECUTION_READY"

    return GateDecision(
        symbol=symbol.upper(),
        status=status,
        no_trade=status != "EXECUTION_READY",
        checks=checks,
        reasons=reasons,
        edge_observable=setup,
        edge_validated=_combine(status_of(
            ("oos_expectancy_positive", "parameters_in_tested_zone", "probabilities_calibrated")
        )),
        reward_sufficient=_combine(status_of(("net_ev_positive", "ev_margin_sufficient"))),
    )
