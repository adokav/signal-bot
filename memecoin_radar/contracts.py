"""Fail-closed contracts for the Solana memecoin opportunity radar.

This package is intentionally provider-agnostic. It defines the decision and
execution boundaries first so market-data adapters cannot silently turn missing
or weak evidence into an investable result.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class Qualification(str, Enum):
    REJECT = "REJECT"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"
    WATCH = "WATCH"
    QUALIFIED = "QUALIFIED"
    ENTRY_SETUP = "ENTRY_SETUP"


class GateState(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class ExecutionMode(str, Enum):
    SHADOW = "SHADOW"
    CONFIRM = "CONFIRM"
    AUTO = "AUTO"


HARD_GATES = (
    "IDENTITY",
    "CONTRACT",
    "LIQUIDITY",
    "OWNERSHIP",
    "MANIPULATION",
    "DATA_QUALITY",
    "EXECUTION",
)


@dataclass(frozen=True)
class GateEvidence:
    gate: str
    state: GateState
    reason: str
    source: str
    observed_at: int
    available_at: int

    def __post_init__(self) -> None:
        gate = self.gate.upper()
        if gate not in HARD_GATES:
            raise ValueError(f"unsupported gate: {gate}")
        object.__setattr__(self, "gate", gate)
        if not self.reason.strip() or not self.source.strip():
            raise ValueError("gate evidence requires reason and source")
        if self.observed_at <= 0 or self.available_at < self.observed_at:
            raise ValueError("invalid evidence timestamps")


@dataclass(frozen=True)
class MemecoinAssessment:
    chain: str
    token_address: str
    symbol: str
    decision_at: int
    gates: Mapping[str, GateEvidence]
    qualification: Qualification
    reasons: tuple[str, ...] = ()
    opportunity_score: float | None = None
    can_authorize_trade: bool = False

    def __post_init__(self) -> None:
        if self.chain.upper() != "SOLANA":
            raise ValueError("v1 memecoin radar is Solana-only")
        if not self.token_address.strip():
            raise ValueError("token address is required")
        if self.decision_at <= 0:
            raise ValueError("invalid decision timestamp")
        if self.can_authorize_trade:
            raise ValueError("research assessment cannot authorize a trade")

        gate_names = {name.upper() for name in self.gates}
        if gate_names != set(HARD_GATES):
            raise ValueError("assessment must contain every hard gate exactly once")
        for name, evidence in self.gates.items():
            if name.upper() != evidence.gate:
                raise ValueError("gate map key does not match evidence gate")
            if evidence.available_at > self.decision_at:
                raise ValueError("future-unavailable evidence cannot enter a decision")

        states = {item.state for item in self.gates.values()}
        if GateState.FAIL in states and self.qualification is not Qualification.REJECT:
            raise ValueError("failed hard gate must produce REJECT")
        if GateState.UNKNOWN in states and self.qualification not in {
            Qualification.REJECT,
            Qualification.DATA_INSUFFICIENT,
        }:
            raise ValueError("unknown hard gate cannot produce WATCH/QUALIFIED/ENTRY_SETUP")
        if self.qualification in {Qualification.REJECT, Qualification.DATA_INSUFFICIENT}:
            if self.opportunity_score is not None:
                raise ValueError("rejected/incomplete token must not receive opportunity score")
        elif self.opportunity_score is not None and not 0.0 <= self.opportunity_score <= 100.0:
            raise ValueError("opportunity score must be between 0 and 100")


@dataclass(frozen=True)
class ExecutionPolicy:
    """Execution boundary for the first integration slice.

    CONFIRM/AUTO exist as future modes in the domain model, but v1 is required
    to remain SHADOW until a separately reviewed signing/execution gateway exists.
    """

    mode: ExecutionMode = ExecutionMode.SHADOW
    max_slippage_bps: int = 0
    max_position_usd: float = 0.0
    max_daily_loss_usd: float = 0.0
    max_open_positions: int = 0
    kill_switch: bool = True
    signing_enabled: bool = False

    def __post_init__(self) -> None:
        if self.mode is not ExecutionMode.SHADOW:
            raise ValueError("memecoin v1 integration must remain SHADOW")
        if self.signing_enabled:
            raise ValueError("signing is not available in memecoin v1")
        if any((self.max_slippage_bps, self.max_position_usd, self.max_daily_loss_usd, self.max_open_positions)):
            raise ValueError("execution limits stay zero until execution gateway review")
        if not self.kill_switch:
            raise ValueError("kill switch must stay engaged in SHADOW")
