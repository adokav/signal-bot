"""Candidate output contract (spec §39).

Every field the spec asks for is present. A value the system cannot
honestly produce stays ``None`` and is rendered as missing — it is never
filled with a placeholder number (a blank probability must not read as
50%, a blank EV must not read as 0).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CandidateReport:
    symbol: str
    status: str
    market_regime: str | None
    setup_type: str | None
    quant_score: float | None
    calibrated_success_probability: float | None
    expected_return_pct: float | None
    expected_loss_pct: float | None
    expected_value_pct: float | None
    entry_zone: tuple[float, float] | None
    invalidation_level: float | None
    stop: float | None
    target_logic: str | None
    estimated_fees_pct: float | None
    estimated_slippage_pct: float | None
    expected_holding_period_hours: float | None
    position_risk_pct: float | None
    correlation_risk: str | None
    confidence_quality: str
    data_quality: str
    gate_reasons: tuple[str, ...] = ()
    can_authorize_trade: bool = False

    def __post_init__(self) -> None:
        if self.can_authorize_trade:
            raise ValueError("a research candidate cannot authorize a trade")
        for name in (
            "quant_score", "calibrated_success_probability", "expected_return_pct",
            "expected_loss_pct", "expected_value_pct", "invalidation_level", "stop",
            "estimated_fees_pct", "estimated_slippage_pct",
            "expected_holding_period_hours", "position_risk_pct",
        ):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite or None")
        p = self.calibrated_success_probability
        if p is not None and not 0.0 <= p <= 1.0:
            raise ValueError("probability must be within [0, 1]")

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["entry_zone"] = list(self.entry_zone) if self.entry_zone else None
        payload["gate_reasons"] = list(self.gate_reasons)
        return payload


def _num(value: float | None, fmt: str, missing: str = "YOK") -> str:
    return missing if value is None else format(value, fmt)


def render_evidence_lines(candidate: CandidateReport) -> list[str]:
    """Turkish summary lines for the fields the radars cannot yet evidence."""

    return [
        f"Durum: {candidate.status} · Veri: {candidate.data_quality}",
        "Kalibre başarı olasılığı: "
        + _num(candidate.calibrated_success_probability, ".0%", "YOK (kalibre edilmedi)"),
        "Beklenen değer (maliyet sonrası): "
        + _num(candidate.expected_value_pct, "+.2f", "YOK (tarihsel sonuç yok)")
        + ("%" if candidate.expected_value_pct is not None else ""),
        f"Güven kalitesi: {candidate.confidence_quality}",
    ]
