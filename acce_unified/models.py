"""Small serialisable contracts shared by every radar source."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class CexTicker:
    symbol: str
    last_price: float
    change_pct: float
    quote_volume: float
    venue: str = "UNKNOWN"


@dataclass(frozen=True)
class RadarCandidate:
    source: str
    key: str
    symbol: str
    score: int
    stage: str
    role: str
    execution_eligible: bool = False
    reasons: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RadarSnapshot:
    generated_at: int
    mode: str
    cex_candidates: tuple[RadarCandidate, ...] = ()
    dex_candidates: tuple[RadarCandidate, ...] = ()
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "mode": self.mode,
            "cex_candidates": [item.to_dict() for item in self.cex_candidates],
            "dex_candidates": [item.to_dict() for item in self.dex_candidates],
            "errors": list(self.errors),
            "can_authorize_trade": False,
        }
