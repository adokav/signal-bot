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
    listing_top_n: int = 5
    listing_min_score: int = 52
    listing_max_candidates: int = 20
    listing_seen_file: str = "mexc_seen_symbols.json"
    listing_candidate_file: str = "mexc_listing_candidates.json"
    listing_candidate_ttl_hours: int = 72
    social_enabled: bool = True
    social_gdelt_enabled: bool = True
    social_cache_ttl_seconds: int = 10 * 60
    social_max_assets: int = 6
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
            social_enabled=_env_bool("UNIFIED_SOCIAL_RADAR_ENABLED", True),
            social_gdelt_enabled=_env_bool("UNIFIED_SOCIAL_GDELT_ENABLED", True),
            social_cache_ttl_seconds=_env_int(
                "UNIFIED_SOCIAL_CACHE_TTL_SECONDS", 10 * 60, 60
            ),
            social_max_assets=min(
                12,
                _env_int("UNIFIED_SOCIAL_MAX_ASSETS", 6, 1),
            ),
            request_timeout_seconds=_env_int("UNIFIED_REQUEST_TIMEOUT_SECONDS", 15, 3),
        )
