"""MEXC discovery primitives used by Signal Bot v5 Core.

Importing this package has no side effects: no thread, polling loop or optional
radar is started automatically. The application entry point owns lifecycle.
"""

from .config import DEFAULT_TRADE_UNIVERSE, UnifiedConfig, build_trade_universe
from .engine import (
    UnifiedRadarEngine,
    UnifiedRadarRuntime,
    attach_snapshot_to_results,
    format_filtered_listing_report,
    format_liquid_long_report,
    format_listing_report,
    format_snapshot_brief,
)
from .models import CexTicker, MexcListing, RadarCandidate, RadarSnapshot

__all__ = [
    "CexTicker",
    "DEFAULT_TRADE_UNIVERSE",
    "MexcListing",
    "RadarCandidate",
    "RadarSnapshot",
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
