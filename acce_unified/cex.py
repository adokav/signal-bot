"""CEX symbol identity helpers shared by the liquid-long radar.

`is_stable_or_synthetic`, `is_leveraged_token` and `opportunity_proxy` are
still consumed by `liquid_long.py` to keep the liquid universe free of
stablecoins and leveraged tokens. The full PriceMonitorX ranking that used
to live here was removed with Dead Code Wave 2 — the bot UI never read its
output.
"""

from __future__ import annotations

import math
import re


STABLE_EXACT = {
    "USDT", "USDC", "BUSD", "TUSD", "DAI", "FDUSD", "USDP", "GUSD",
    "USD1", "USDE", "USDS", "USDD", "USDX", "PYUSD", "RLUSD", "EURC",
    "EURI", "AEUR", "FRAX", "LUSD", "SUSD", "EUR", "GBP", "TRY",
}
STABLE_RE = re.compile(r"^(?:USD[A-Z0-9]*|[A-Z0-9]*USD|EUR[A-Z0-9]*|[A-Z0-9]*EUR)$")
LEVERAGED_SUFFIXES = (
    "UP", "DOWN", "BULL", "BEAR",
    "2L", "2S", "3L", "3S", "4L", "4S", "5L", "5S",
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def is_stable_or_synthetic(base: str) -> bool:
    value = base.upper()
    return value in STABLE_EXACT or bool(STABLE_RE.match(value))


def is_leveraged_token(base: str, all_bases: set[str]) -> bool:
    for suffix in LEVERAGED_SUFFIXES:
        if base.endswith(suffix) and len(base) > len(suffix):
            if base[: -len(suffix)] in all_bases:
                return True
    return False


def opportunity_proxy(change_pct: float, quote_volume: float) -> float:
    """Cheap first-pass score retained from PriceMonitorX v2."""

    if quote_volume <= 0:
        return -1e9
    liquidity = _clamp(math.log10(max(quote_volume, 1.0)) - 5.5, 0.0, 4.0)
    if -8.0 <= change_pct < 0.0:
        momentum = 0.4 + (change_pct + 8.0) / 20.0
    elif 0.0 <= change_pct <= 18.0:
        momentum = 1.0 + change_pct / 9.0
    elif 18.0 < change_pct <= 35.0:
        momentum = 3.0 - (change_pct - 18.0) / 17.0
    elif change_pct > 35.0:
        momentum = max(-2.0, 1.0 - (change_pct - 35.0) / 15.0)
    else:
        momentum = -1.5
    return liquidity + momentum
