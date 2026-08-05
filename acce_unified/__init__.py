"""MEXC discovery primitives used by Signal Bot v5 Core.

Importing this package has no side effects: no thread, polling loop or optional
radar is started automatically. The application entry point owns lifecycle.
"""

from .config import DEFAULT_TRADE_UNIVERSE, UnifiedConfig, build_trade_universe
from .engine import (
    UnifiedRadarEngine as CoreUnifiedRadarEngine,
    UnifiedRadarRuntime,
    attach_snapshot_to_results,
    format_filtered_listing_report,
    format_liquid_long_report,
    format_listing_report,
    format_snapshot_brief,
)
from .listing_integrity import (
    StrictNewListingRadarEngine,
    candidate_integrity_reasons,
    sanitize_listing_snapshot,
)
from .models import CexTicker, MexcListing, RadarCandidate, RadarSnapshot
from .research import (
    CostEstimate,
    Decision,
    Direction,
    Evidence,
    EvidenceStatus,
    MarketState,
    Opportunity,
    OutcomeLabel,
    ReplayClock,
    ResearchStore,
)

UnifiedRadarEngine = StrictNewListingRadarEngine

__all__ = [
    "CexTicker",
    "CoreUnifiedRadarEngine",
    "CostEstimate",
    "DEFAULT_TRADE_UNIVERSE",
    "Decision",
    "Direction",
    "Evidence",
    "EvidenceStatus",
    "MarketState",
    "MexcListing",
    "Opportunity",
    "OutcomeLabel",
    "RadarCandidate",
    "RadarSnapshot",
    "ReplayClock",
    "ResearchStore",
    "StrictNewListingRadarEngine",
    "UnifiedConfig",
    "UnifiedRadarEngine",
    "UnifiedRadarRuntime",
    "attach_snapshot_to_results",
    "build_trade_universe",
    "candidate_integrity_reasons",
    "format_filtered_listing_report",
    "format_liquid_long_report",
    "format_listing_report",
    "format_snapshot_brief",
    "sanitize_listing_snapshot",
]
