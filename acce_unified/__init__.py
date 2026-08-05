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
from .models import CexTicker, MexcListing, RadarCandidate, RadarSnapshot
from .observation_archive import (
    LiquidObservationArchive,
    LiquidScanObservation,
    LiquidSymbolObservation,
    ObservationArchivingCexProvider,
    ObservationArchivingRadarEngine,
)
from .observation_runtime import (
    CadencedObservationArchivingCexProvider,
    ProductionUnifiedRadarEngine,
)
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

UnifiedRadarEngine = ProductionUnifiedRadarEngine

__all__ = [
    "CadencedObservationArchivingCexProvider",
    "CexTicker",
    "CoreUnifiedRadarEngine",
    "CostEstimate",
    "DEFAULT_TRADE_UNIVERSE",
    "Decision",
    "Direction",
    "Evidence",
    "EvidenceStatus",
    "LiquidObservationArchive",
    "LiquidScanObservation",
    "LiquidSymbolObservation",
    "MarketState",
    "MexcListing",
    "ObservationArchivingCexProvider",
    "ObservationArchivingRadarEngine",
    "Opportunity",
    "OutcomeLabel",
    "ProductionUnifiedRadarEngine",
    "RadarCandidate",
    "RadarSnapshot",
    "ReplayClock",
    "ResearchStore",
    "UnifiedConfig",
    "UnifiedRadarEngine",
    "UnifiedRadarRuntime",
    "attach_snapshot_to_results",
    "build_trade_universe",
    "format_filtered_listing_report",
    "format_liquid_long_report",
    "format_listing_report",
    "format_snapshot_brief",
]
