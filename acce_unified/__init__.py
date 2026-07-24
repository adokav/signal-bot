"""ACCE unified discovery and validation layer.

The package deliberately separates *discovery* from *trade authorization*.
Signal Bot remains the only component allowed to build a trade plan; CEX and
MEXC new-listing radars can only add evidence while the integration is in
shadow mode.
"""

import logging

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


def _start_optional_robinhood_radar() -> None:
    """Start the MEXC-gated push-only sidecar once on package import."""

    try:
        from robinhood_mexc_radar import start_background_radar

        start_background_radar()
    except Exception as exc:  # optional discovery must never block the main bot
        logging.getLogger(__name__).warning(
            "Robinhood x MEXC radar sidecar could not start: %s", exc
        )


_start_optional_robinhood_radar()
