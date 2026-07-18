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
    dex_enabled: bool = True
    scan_interval_seconds: int = 30 * 60
    cex_top_n: int = 12
    cex_min_quote_volume: float = 500_000.0
    dex_top_n: int = 8
    dex_min_liquidity: float = 35_000.0
    dex_min_h1_transactions: int = 24
    dex_max_age_hours: int = 45 * 24
    dex_max_tokens_per_chain: int = 90
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
            dex_enabled=_env_bool("UNIFIED_DEX_RADAR_ENABLED", True),
            scan_interval_seconds=_env_int("UNIFIED_SCAN_INTERVAL_SECONDS", 30 * 60, 300),
            cex_top_n=_env_int("UNIFIED_CEX_TOP_N", 12, 1),
            cex_min_quote_volume=_env_float("UNIFIED_CEX_MIN_QUOTE_VOLUME", 500_000.0, 0.0),
            dex_top_n=_env_int("UNIFIED_DEX_TOP_N", 8, 1),
            dex_min_liquidity=_env_float("UNIFIED_DEX_MIN_LIQUIDITY", 35_000.0, 0.0),
            dex_min_h1_transactions=_env_int("UNIFIED_DEX_MIN_H1_TRANSACTIONS", 24, 0),
            dex_max_age_hours=_env_int("UNIFIED_DEX_MAX_AGE_HOURS", 45 * 24, 1),
            dex_max_tokens_per_chain=_env_int("UNIFIED_DEX_MAX_TOKENS_PER_CHAIN", 90, 1),
            request_timeout_seconds=_env_int("UNIFIED_REQUEST_TIMEOUT_SECONDS", 15, 3),
        )
