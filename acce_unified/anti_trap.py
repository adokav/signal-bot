"""Conservative anti-trap assessment for MEXC new-listing candidates.

The raw listing score measures visibility and market activity.  This module
separates that from manipulation/trap risk.  It never authorizes an order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class AntiTrapAssessment:
    opportunity_score: int
    trap_risk_score: int
    intelligence_quality: int
    conservative_score: int
    state: str
    vetoed: bool
    opportunity_reasons: tuple[str, ...]
    trap_reasons: tuple[str, ...]
    veto_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_score": self.opportunity_score,
            "trap_risk_score": self.trap_risk_score,
            "intelligence_quality": self.intelligence_quality,
            "conservative_score": self.conservative_score,
            "state": self.state,
            "vetoed": self.vetoed,
            "opportunity_reasons": list(self.opportunity_reasons),
            "trap_reasons": list(self.trap_reasons),
            "veto_reasons": list(self.veto_reasons),
            "can_authorize_trade": False,
        }


def assess_new_listing(candidate: Mapping[str, Any]) -> AntiTrapAssessment:
    """Split visible momentum from trap risk and incomplete intelligence.

    This first version is deliberately conservative.  It treats missing data as
    uncertainty rather than as evidence of safety and uses hard vetoes for risks
    that should never be hidden by a high headline score.
    """

    metadata = candidate.get("metadata") or {}
    fundamentals = metadata.get("fundamentals") or {}
    social = metadata.get("social") or {}

    raw_score = int(_clamp(float(candidate.get("score") or 0)))
    opportunity = raw_score
    trap = 0.0
    quality = 100.0
    opportunity_reasons: list[str] = []
    trap_reasons: list[str] = []
    veto_reasons: list[str] = []

    quote_volume = float(metadata.get("quote_volume") or 0.0)
    acceleration = float(metadata.get("volume_acceleration") or 0.0)
    spread_bps = float(metadata.get("spread_bps") or 0.0)
    change_pct = float(metadata.get("change_pct") or 0.0)

    if quote_volume >= 2_000_000 and 1.0 <= acceleration <= 4.0:
        opportunity_reasons.append("hacim ve ivme birlikte yeterli")
    if 0 < spread_bps <= 35:
        opportunity_reasons.append("işlem makası uygulanabilir")

    # Thin volume plus sudden acceleration is easy to manufacture.
    if quote_volume < 250_000 and acceleration >= 1.25:
        trap += 30
        trap_reasons.append("düşük mutlak hacimde ani ivme")
    elif quote_volume < 500_000:
        trap += 18
        trap_reasons.append("mutlak hacim zayıf")

    if spread_bps <= 0:
        quality -= 15
        trap += 8
        trap_reasons.append("spread doğrulanamadı")
    elif spread_bps > 150:
        trap += 45
        veto_reasons.append("spread kabul edilemez")
    elif spread_bps > 80:
        trap += 28
        trap_reasons.append("spread çok geniş")

    if change_pct > 80:
        trap += 45
        veto_reasons.append("ilk hareket aşırı uzamış")
    elif change_pct > 50:
        trap += 25
        trap_reasons.append("fiyat hareketi kalabalıklaşmış")
    elif change_pct < -35:
        trap += 30
        trap_reasons.append("listeleme sonrası ağır dağıtım")

    fundamental_status = str(fundamentals.get("status") or "DATA_PENDING").upper()
    fundamental_stage = str(fundamentals.get("stage") or "DATA_PENDING").upper()
    identity_confidence = int(fundamentals.get("identity_confidence") or 0)
    circulation = fundamentals.get("circulation_pct")

    if fundamental_status != "READY":
        quality -= 35
        trap += 15
        trap_reasons.append(f"temel istihbarat eksik: {fundamental_status}")
    if fundamental_stage == "HIGH_RISK":
        trap += 45
        veto_reasons.append("temel metrikler yüksek risk")
    if fundamental_status == "READY" and identity_confidence < 80:
        quality -= 25
        veto_reasons.append("token kimliği yeterince doğrulanmadı")
    if fundamentals.get("circulating_supply") is None:
        quality -= 20
        trap += 15
        trap_reasons.append("dolaşımdaki arz doğrulanmadı")
    if circulation is not None and float(circulation) < 15:
        trap += 35
        veto_reasons.append("çok düşük dolaşım / yüksek açılım riski")
    elif circulation is not None and float(circulation) < 25:
        trap += 20
        trap_reasons.append("düşük dolaşım oranı")

    social_status = str(social.get("status") or "UNAVAILABLE").upper()
    social_stage = str(social.get("stage") or "DATA_PENDING").upper()
    if social_stage in {"SUSPICIOUS", "NEGATIVE_EVENT"}:
        trap += 45
        veto_reasons.append(f"sosyal veri riskli: {social_stage}")
    elif social_status not in {"READY", "OK"}:
        quality -= 20
        trap += 8
        trap_reasons.append(f"sosyal istihbarat eksik: {social_status}")

    quality_i = int(round(_clamp(quality)))
    trap_i = int(round(_clamp(trap)))
    # Low intelligence quality is itself a capital-protection penalty.
    uncertainty_penalty = (100 - quality_i) * 0.35
    conservative = int(round(_clamp(opportunity - trap_i - uncertainty_penalty)))

    vetoed = bool(veto_reasons)
    if vetoed:
        state = "TRAP_RISK"
    elif quality_i < 60:
        state = "INTELLIGENCE_INCOMPLETE"
    elif conservative >= 70 and trap_i <= 20:
        # This is only a candidate state; multi-scan persistence is still needed.
        state = "BASE_BUILDING"
    elif conservative >= 50:
        state = "PRICE_DISCOVERY"
    else:
        state = "DISCOVERED"

    return AntiTrapAssessment(
        opportunity_score=opportunity,
        trap_risk_score=trap_i,
        intelligence_quality=quality_i,
        conservative_score=conservative,
        state=state,
        vetoed=vetoed,
        opportunity_reasons=tuple(opportunity_reasons),
        trap_reasons=tuple(trap_reasons),
        veto_reasons=tuple(veto_reasons),
    )
