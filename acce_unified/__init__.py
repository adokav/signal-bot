"""ACCE unified discovery and validation layer.

The package deliberately separates *discovery* from *trade authorization*.
Signal Bot remains the only component allowed to build a trade plan; CEX and
DEX radars can only add evidence while the integration is in shadow mode.
"""

from .config import DEFAULT_TRADE_UNIVERSE, UnifiedConfig, build_trade_universe
from .engine import (
    UnifiedRadarEngine,
    UnifiedRadarRuntime,
    attach_snapshot_to_results,
    format_snapshot_brief,
)
from .models import CexTicker, RadarCandidate, RadarSnapshot

__all__ = [
    "CexTicker",
    "DEFAULT_TRADE_UNIVERSE",
    "RadarCandidate",
    "RadarSnapshot",
    "UnifiedConfig",
    "UnifiedRadarEngine",
    "UnifiedRadarRuntime",
    "attach_snapshot_to_results",
    "build_trade_universe",
    "format_snapshot_brief",
]
