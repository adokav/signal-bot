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
    bid_price: float = 0.0
    ask_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0


@dataclass(frozen=True)
class MexcListing:
    symbol: str
    pair: str
    title: str
    rank: int
    spot_status: str
    last_price: float
    change_pct: float
    quote_volume: float
    volume_acceleration: float
    discovery_source: str = "ANNOUNCEMENT"
    first_seen_at: int = 0
    spread_bps: float = 0.0
    manually_watched: bool = False
    social_data: dict[str, Any] = field(default_factory=dict)
    fundamental_data: dict[str, Any] = field(default_factory=dict)


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
    liquid_long_candidates: tuple[RadarCandidate, ...] = ()
    liquid_universe_size: int = 0
    liquid_enriched_size: int = 0
    liquid_supply_ready_size: int = 0
    liquid_market_context: dict[str, Any] = field(default_factory=dict)
    listing_candidates: tuple[RadarCandidate, ...] = ()
    listing_filtered_candidates: tuple[RadarCandidate, ...] = ()
    social_candidates: tuple[RadarCandidate, ...] = ()
    fundamental_candidates: tuple[RadarCandidate, ...] = ()
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "mode": self.mode,
            "cex_candidates": [item.to_dict() for item in self.cex_candidates],
            "liquid_long_candidates": [
                item.to_dict() for item in self.liquid_long_candidates
            ],
            "liquid_universe_size": self.liquid_universe_size,
            "liquid_enriched_size": self.liquid_enriched_size,
            "liquid_supply_ready_size": self.liquid_supply_ready_size,
            "liquid_market_context": dict(self.liquid_market_context),
            "listing_candidates": [item.to_dict() for item in self.listing_candidates],
            "listing_filtered_candidates": [
                item.to_dict() for item in self.listing_filtered_candidates
            ],
            "social_candidates": [item.to_dict() for item in self.social_candidates],
            "fundamental_candidates": [
                item.to_dict() for item in self.fundamental_candidates
            ],
            "errors": list(self.errors),
            "can_authorize_trade": False,
        }
