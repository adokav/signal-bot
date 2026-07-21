"""Configuration and the single canonical capital universe.

PriceMonitorX and PhenomenonX are discovery inputs.  They are intentionally
not allowed to grow the executable universe by themselves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_TRADE_UNIVERSE: dict[str, str] = {
    "BTCUSDT": "CORE",
    "ETHUSDT": "CORE",
    "SOLUSDT": "HIGH_BETA",
    "LINKUSDT": "MAJOR_ALT",
    "ONDOUSDT": "HIGH_BETA",
    "RENDERUSDT": "HIGH_BETA",
    "PYTHUSDT": "HIGH_BETA",
    "BONKUSDT": "MEME",
    "POPCATUSDT": "MEME",
}

VALID_GROUPS = {"CORE", "MAJOR_ALT", "HIGH_BETA", "MEME"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "evet"}


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _normalise_symbol(raw: str, quote_asset: str = "USDT") -> str:
    value = raw.strip().upper().replace("/", "")
    if not value:
        return ""
    return value if value.endswith(quote_asset) else f"{value}{quote_asset}"


def build_trade_universe(raw: str | None = None) -> dict[str, str]:
    """Return the executable universe.

    ``TRADE_UNIVERSE`` accepts ``SYMBOL[:GROUP]`` comma-separated entries.
    An invalid/empty override fails closed to the reviewed nine-asset default.
    """

    value = os.getenv("TRADE_UNIVERSE", "") if raw is None else raw
    if not value.strip():
        return dict(DEFAULT_TRADE_UNIVERSE)

    parsed: dict[str, str] = {}
    for item in value.split(","):
        token = item.strip()
        if not token:
            continue
        symbol_raw, sep, group_raw = token.partition(":")
        symbol = _normalise_symbol(symbol_raw)
        if not symbol:
            continue
        inferred = DEFAULT_TRADE_UNIVERSE.get(symbol, "HIGH_BETA")
        group = group_raw.strip().upper() if sep else inferred
        parsed[symbol] = group if group in VALID_GROUPS else inferred
    return parsed or dict(DEFAULT_TRADE_UNIVERSE)


@dataclass(frozen=True)
class UnifiedConfig:
    enabled: bool = True
    mode: str = "SHADOW"
    cex_enabled: bool = True
    listing_enabled: bool = True
    scan_interval_seconds: int = 2 * 60
    cex_top_n: int = 12
    cex_min_quote_volume: float = 500_000.0
    liquid_universe_size: int = 100
    liquid_enrichment_size: int = 100
    liquid_fundamental_size: int = 100
    liquid_long_top_n: int = 5
    liquid_min_quote_volume: float = 1_000_000.0
    liquid_max_24h_drawdown_pct: float = -8.0
    liquid_max_24h_gain_pct: float = 25.0
    liquid_max_spread_bps: float = 35.0
    liquid_min_long_score: int = 64
    liquid_require_supply_data: bool = True
    listing_top_n: int = 5
    listing_min_score: int = 52
    listing_max_candidates: int = 20
    listing_seen_file: str = "mexc_seen_symbols.json"
    listing_candidate_file: str = "mexc_listing_candidates.json"
    listing_candidate_ttl_hours: int = 72
    listing_confirmation_scans: int = 2
    listing_max_drawdown_pct: float = -8.0
    listing_require_open_spot: bool = True
    listing_require_supply_data: bool = True
    social_enabled: bool = True
    social_reddit_enabled: bool = True
    social_gdelt_enabled: bool = True
    social_cache_ttl_seconds: int = 10 * 60
    social_max_assets: int = 6
    social_window_hours: int = 6
    social_min_mentions: int = 4
    social_min_unique_authors: int = 3
    social_min_community_platforms: int = 1
    social_min_sentiment: int = -10
    social_alert_gate_required: bool = True
    social_alert_cooldown_seconds: int = 6 * 60 * 60
    fundamental_enabled: bool = True
    fundamental_cache_ttl_seconds: int = 30 * 60
    fundamental_max_assets: int = 100
    request_timeout_seconds: int = 15

    @classmethod
    def from_env(cls) -> "UnifiedConfig":
        requested_mode = os.getenv("UNIFIED_ENGINE_MODE", "SHADOW").strip().upper()
        # Only non-authoritative modes exist. A typo or a future LIVE value
        # cannot silently grant the discovery layer execution power.
        mode = requested_mode if requested_mode in {"SHADOW", "ADVISORY"} else "SHADOW"
        return cls(
            enabled=_env_bool("UNIFIED_ENGINE_ENABLED", True),
            mode=mode,
            cex_enabled=_env_bool("UNIFIED_CEX_RADAR_ENABLED", True),
            listing_enabled=_env_bool("UNIFIED_LISTING_RADAR_ENABLED", True),
            scan_interval_seconds=_env_int("UNIFIED_SCAN_INTERVAL_SECONDS", 2 * 60, 60),
            cex_top_n=_env_int("UNIFIED_CEX_TOP_N", 12, 1),
            cex_min_quote_volume=_env_float("UNIFIED_CEX_MIN_QUOTE_VOLUME", 500_000.0, 0.0),
            liquid_universe_size=min(
                200, _env_int("UNIFIED_LIQUID_UNIVERSE_SIZE", 100, 20)
            ),
            liquid_enrichment_size=min(
                100, _env_int("UNIFIED_LIQUID_ENRICHMENT_SIZE", 100, 20)
            ),
            liquid_fundamental_size=min(
                100, _env_int("UNIFIED_LIQUID_FUNDAMENTAL_SIZE", 100, 20)
            ),
            liquid_long_top_n=min(
                10, _env_int("UNIFIED_LIQUID_LONG_TOP_N", 5, 1)
            ),
            liquid_min_quote_volume=_env_float(
                "UNIFIED_LIQUID_MIN_QUOTE_VOLUME", 1_000_000.0, 0.0
            ),
            liquid_max_24h_drawdown_pct=min(
                0.0,
                _env_float("UNIFIED_LIQUID_MAX_24H_DRAWDOWN_PCT", -8.0, -100.0),
            ),
            liquid_max_24h_gain_pct=_env_float(
                "UNIFIED_LIQUID_MAX_24H_GAIN_PCT", 25.0, 0.0
            ),
            liquid_max_spread_bps=_env_float(
                "UNIFIED_LIQUID_MAX_SPREAD_BPS", 35.0, 1.0
            ),
            liquid_min_long_score=min(
                100, _env_int("UNIFIED_LIQUID_MIN_LONG_SCORE", 64, 0)
            ),
            liquid_require_supply_data=_env_bool(
                "UNIFIED_LIQUID_REQUIRE_SUPPLY_DATA", True
            ),
            listing_top_n=min(10, _env_int("UNIFIED_LISTING_TOP_N", 5, 1)),
            listing_min_score=min(100, _env_int("UNIFIED_LISTING_MIN_SCORE", 52, 0)),
            listing_max_candidates=min(
                40,
                _env_int("UNIFIED_LISTING_MAX_CANDIDATES", 20, 1),
            ),
            listing_seen_file=os.getenv("UNIFIED_LISTING_SEEN_FILE", "mexc_seen_symbols.json").strip()
            or "mexc_seen_symbols.json",
            listing_candidate_file=os.getenv(
                "UNIFIED_LISTING_CANDIDATE_FILE", "mexc_listing_candidates.json"
            ).strip() or "mexc_listing_candidates.json",
            listing_candidate_ttl_hours=min(
                168,
                _env_int("UNIFIED_LISTING_CANDIDATE_TTL_HOURS", 72, 1),
            ),
            listing_confirmation_scans=min(
                4, _env_int("UNIFIED_LISTING_CONFIRMATION_SCANS", 2, 2)
            ),
            listing_max_drawdown_pct=min(
                0.0,
                _env_float("UNIFIED_LISTING_MAX_DRAWDOWN_PCT", -8.0, -100.0),
            ),
            listing_require_open_spot=_env_bool(
                "UNIFIED_LISTING_REQUIRE_OPEN_SPOT", True
            ),
            listing_require_supply_data=_env_bool(
                "UNIFIED_LISTING_REQUIRE_SUPPLY_DATA", True
            ),
            social_enabled=_env_bool("UNIFIED_SOCIAL_RADAR_ENABLED", True),
            social_reddit_enabled=_env_bool("UNIFIED_SOCIAL_REDDIT_ENABLED", True),
            social_gdelt_enabled=_env_bool("UNIFIED_SOCIAL_GDELT_ENABLED", True),
            social_cache_ttl_seconds=_env_int(
                "UNIFIED_SOCIAL_CACHE_TTL_SECONDS", 10 * 60, 60
            ),
            social_max_assets=min(
                12,
                _env_int("UNIFIED_SOCIAL_MAX_ASSETS", 6, 1),
            ),
            social_window_hours=min(
                24,
                _env_int("UNIFIED_SOCIAL_WINDOW_HOURS", 6, 1),
            ),
            social_min_mentions=_env_int("UNIFIED_SOCIAL_MIN_MENTIONS", 4, 1),
            social_min_unique_authors=_env_int(
                "UNIFIED_SOCIAL_MIN_UNIQUE_AUTHORS", 3, 1
            ),
            social_min_community_platforms=min(
                2,
                _env_int("UNIFIED_SOCIAL_MIN_PLATFORMS", 1, 1),
            ),
            social_min_sentiment=min(
                100,
                _env_int("UNIFIED_SOCIAL_MIN_SENTIMENT", -10, -100),
            ),
            social_alert_gate_required=_env_bool(
                "UNIFIED_SOCIAL_ALERT_GATE_REQUIRED", True
            ),
            social_alert_cooldown_seconds=_env_int(
                "UNIFIED_SOCIAL_ALERT_COOLDOWN_SECONDS", 6 * 60 * 60, 0
            ),
            fundamental_enabled=_env_bool("UNIFIED_FUNDAMENTAL_RADAR_ENABLED", True),
            fundamental_cache_ttl_seconds=_env_int(
                "UNIFIED_FUNDAMENTAL_CACHE_TTL_SECONDS", 30 * 60, 300
            ),
            fundamental_max_assets=min(
                120,
                _env_int("UNIFIED_FUNDAMENTAL_MAX_ASSETS", 100, 1),
            ),
            request_timeout_seconds=_env_int("UNIFIED_REQUEST_TIMEOUT_SECONDS", 15, 3),
        )
